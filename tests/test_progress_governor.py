from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from villani_code.progress_governor import ProgressGovernor, ProgressSnapshot, parse_progress_verdict
from villani_code.state import Runner
from villani_code import state_runtime


class SequencedClient:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.payloads: list[dict[str, Any]] = []

    def create_message(self, payload: dict[str, Any], stream: bool):
        self.payloads.append(payload)
        if not self.responses:
            return {"content": [{"type": "text", "text": "done"}]}
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def assistant_tool(name: str, tool_id: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}], "stop_reason": "tool_use"}


def assistant_text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def verdict(action="redirect", confidence="high", instruction="Run one focused verification command before continuing.") -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "action": action,
                        "confidence": confidence,
                        "failure_mode": "wandering",
                        "evidence": "three read-only turns with no workspace revision",
                        "instruction": instruction,
                    }
                ),
            }
        ]
    }


def seed_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=path, check=True)
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def runner(tmp_path: Path, client: SequencedClient, events: list[dict[str, Any]]) -> Runner:
    seed_repo(tmp_path)
    r = Runner(client=client, repo=tmp_path, model="m", stream=False, bypass_permissions=True)
    r.event_callback = events.append
    return r


def test_native_initialisation(tmp_path: Path) -> None:
    seed_repo(tmp_path)
    r = Runner(client=SequencedClient([]), repo=tmp_path, model="m", stream=False)
    assert isinstance(r._progress_governor, ProgressGovernor)
    assert r._governor_interventions_used == 0
    assert r._last_governor_trigger == ""
    assert r._last_governor_workspace_revision == -1
    assert r._last_governor_verdict is None
    assert r._last_reviewed_validation_signature == ""
    assert r._last_verified_workspace_revision == -1


def test_no_premature_call_first_inspection(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    client = SequencedClient([assistant_tool("Read", "t1", {"file_path": "README.md"}), assistant_text("done")])
    runner(tmp_path, client, events).run("Explain the repo")
    assert not any(e.get("type") == "progress_governor_started" for e in events)


def test_bash_tool_result_does_not_crash_on_recent_actions(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    client = SequencedClient([assistant_tool("Bash", "t1", {"command": "printf ok"}), assistant_text("done")])
    result = runner(tmp_path, client, events).run("Run a harmless command")
    assert result["response"]["content"][0]["text"] == "done"


def test_recon_wandering_high_confidence_injected_in_tool_result(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    client = SequencedClient([
        assistant_tool("Read", "t1", {"file_path": "README.md"}),
        assistant_tool("Read", "t2", {"file_path": "README.md"}),
        assistant_tool("Read", "t3", {"file_path": "README.md"}),
        verdict(),
        assistant_text("done"),
    ])
    result = runner(tmp_path, client, events).run("Explain the repo")
    tool_turn = result["messages"][-2]
    assert "<progress_governor>" in tool_turn["content"][-1]["content"]
    assert any(e.get("type") == "progress_governor_verdict" and e.get("intervened") for e in events)


def test_low_confidence_suppression_does_not_mutate_messages(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    client = SequencedClient([
        assistant_tool("Read", "t1", {"file_path": "README.md"}),
        assistant_tool("Read", "t2", {"file_path": "README.md"}),
        assistant_tool("Read", "t3", {"file_path": "README.md"}),
        verdict(confidence="medium"),
        assistant_text("done"),
    ])
    result = runner(tmp_path, client, events).run("Explain the repo")
    assert "<progress_governor>" not in str(result["messages"])
    assert any(e.get("type") == "progress_governor_suppressed" and e.get("reason") == "confidence_not_high" for e in events)


def test_repeated_verification_failure_trigger_helper(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    r = runner(tmp_path, SequencedClient([]), events)
    r._last_validation_target = '["src/app.py"]'
    r._last_validation_summary = "status repeated without new validation evidence"
    r._last_validation_artifact_signature = "[]"
    r._validation_repeated_without_new_evidence = True
    trigger = r._progress_governor.choose_trigger(
        turn_index=3,
        workspace_revision=1,
        consecutive_recon_turns=0,
        edited_this_turn=False,
        changed_files=["src/app.py"],
        intended_targets=["src/app.py"],
    )
    assert trigger == "repeated_verification_failure"


def test_completion_verification_trigger_continues_instead_of_accepting(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
    client = SequencedClient([
        assistant_tool("Patch", "t1", {"file_path": "src/app.py", "unified_diff": diff}),
        assistant_text("complete"),
        verdict(action="verify", instruction="Run python -m pytest -q or explain why no test command applies."),
        assistant_text("complete after verify request"),
    ])
    result = runner(tmp_path, client, events).run("Implement the requested change")
    assert any("<progress_governor>" in str(m.get("content")) for m in result["messages"])
    assert len([p for p in client.payloads if p.get("tools") is None]) >= 1


def test_repeated_run_resets_progress_governor_state(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    client = SequencedClient([
        assistant_tool("Read", "t1", {"file_path": "README.md"}),
        assistant_tool("Read", "t2", {"file_path": "README.md"}),
        assistant_tool("Read", "t3", {"file_path": "README.md"}),
        verdict(),
        assistant_text("done"),
    ])
    r = runner(tmp_path, client, events)
    r.run("Explain the repo")
    assert r._governor_interventions_used == 1
    assert r._last_governor_verdict is not None

    client.responses = [assistant_text("done again")]
    r.run("Explain the repo again")

    assert r._governor_interventions_used == 0
    assert r._last_governor_trigger == ""
    assert r._last_governor_workspace_revision == -1
    assert r._last_governor_verdict is None
    assert r._last_reviewed_validation_signature == ""
    assert r._last_verified_workspace_revision == -1


def test_verified_completion_does_not_interfere(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    r = runner(tmp_path, SequencedClient([]), events)
    assert r._progress_governor.completion_trigger(
        code_change_oriented=True,
        meaningful_repo_edit_made=True,
        workspace_revision=1,
        fresh_passing_verification=True,
    ) == ""


def test_scope_drift_trigger_helper(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    r = runner(tmp_path, SequencedClient([]), events)
    trigger = r._progress_governor.choose_trigger(
        turn_index=3,
        workspace_revision=1,
        consecutive_recon_turns=0,
        edited_this_turn=True,
        changed_files=["src/app.py"],
        intended_targets=["src/other.py"],
    )
    assert trigger == "scope_drift_after_edit"


def test_intervention_cap_suppresses_after_two(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    r = runner(tmp_path, SequencedClient([verdict(), verdict(), verdict()]), events)
    snapshot = ProgressSnapshot("command_wandering", 3, 0, "obj", [], [], 3, 3, "", "", "", False, False, [])
    assert r._progress_governor.review(snapshot)[1] is True
    assert r._progress_governor.review(snapshot)[1] is True
    assert r._progress_governor.review(snapshot)[1] is False
    assert any(e.get("reason") == "intervention_cap_reached" for e in events)


def test_side_call_failure_invalid_json_invalid_schema_nonfatal(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    r = runner(tmp_path, SequencedClient([RuntimeError("boom"), {"content": [{"type": "text", "text": "no"}]}, {"content": [{"type": "text", "text": "{}"}]}]), events)
    snapshot = ProgressSnapshot("command_wandering", 3, 0, "obj", [], [], 3, 3, "", "", "", False, False, [])
    assert r._progress_governor.review(snapshot) == (None, False)
    assert r._progress_governor.review(snapshot) == (None, False)
    assert r._progress_governor.review(snapshot) == (None, False)
    assert [e.get("type") for e in events].count("progress_governor_failed") == 3


def test_anthropic_tool_sequence_integrity_for_injected_tool_result() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "x"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok\n\n<progress_governor>n</progress_governor>", "is_error": False}]},
    ]
    state_runtime.validate_anthropic_tool_sequence(messages)


def test_recovery_collision_prevention_contract(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    r = runner(tmp_path, SequencedClient([verdict(confidence="medium")]), events)
    snapshot = ProgressSnapshot("command_wandering", 3, 0, "obj", [], [], 3, 3, "", "", "", False, False, [])
    assert r._progress_governor.review(snapshot)[1] is False
    assert r._recovery_count == 0
    r2 = runner(tmp_path / "r2", SequencedClient([verdict()]), [])
    assert r2._progress_governor.review(snapshot)[1] is True
