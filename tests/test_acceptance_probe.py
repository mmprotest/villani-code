from __future__ import annotations

from pathlib import Path

from villani_code.execution import ExecutionBudget
from villani_code.state import Runner


class SequenceClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.payloads: list[dict] = []
        self.index = 0

    def create_message(self, payload, stream):
        assert stream is False
        self.payloads.append(payload)
        response = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        return response


class _Hooks:
    def run_event(self, *_args, **_kwargs):
        return type("Hook", (), {"allow": True, "reason": ""})()


class _PermissivePermissions:
    def evaluate_with_reason(self, *_args, **_kwargs):
        from villani_code.permissions import Decision

        return type("P", (), {"decision": Decision.ALLOW, "reason": ""})()


def _tool(name: str, input_: dict, id_: str) -> dict:
    return {"type": "tool_use", "name": name, "input": input_, "id": id_}


def _text(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def _tools(*tools: dict) -> dict:
    return {"role": "assistant", "content": list(tools), "stop_reason": "tool_use"}


def _runner(tmp_path: Path, responses: list[dict], events: list[dict] | None = None) -> Runner:
    runner = Runner(
        client=SequenceClient(responses),
        repo=tmp_path,
        model="m",
        stream=False,
        plan_mode="off",
        auto_accept_edits=True,
        event_callback=(events.append if events is not None else None),
    )
    runner.hooks = _Hooks()
    runner.permissions = _PermissivePermissions()
    return runner


def _budget(turns: int = 8, tools: int = 12) -> ExecutionBudget:
    return ExecutionBudget(max_turns=turns, max_tool_calls=tools, max_seconds=30, max_no_edit_turns=20, max_reconsecutive_recon_turns=20)


def test_required_probe_cannot_complete_before_it_runs(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            _tools(_tool("DefineAcceptanceProbe", {"commands": ["python -c 'print(1)'"]}, "p1")),
            _text("done"),
            _tools(_tool("Bash", {"command": "python -c 'print(1)'"}, "b1")),
            _text("done"),
        ],
    )

    result = runner.run("implement a tiny change", execution_budget=_budget())

    assert result["transcript"]["acceptance_probe"]["acceptance_probe_status"] == "passed"
    assert any("Acceptance probe gate" in str(m.get("content")) for m in result["messages"])


def test_passed_probe_permits_completion(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            _tools(_tool("DefineAcceptanceProbe", {"commands": ["python -c 'print(1)'"]}, "p1")),
            _tools(_tool("Bash", {"command": "python -c 'print(1)'"}, "b1")),
            _text("done"),
        ],
    )

    result = runner.run("implement a tiny change", execution_budget=_budget())

    assert result["transcript"]["acceptance_probe"]["acceptance_probe_status"] == "passed"
    assert result["response"]["content"][0]["text"] == "done"


def test_mutation_after_passed_probe_marks_stale_and_blocks_until_rerun(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            _tools(_tool("DefineAcceptanceProbe", {"commands": ["python -c 'print(1)'"]}, "p1")),
            _tools(_tool("Bash", {"command": "python -c 'print(1)'"}, "b1")),
            _tools(_tool("Write", {"file_path": "a.txt", "content": "x\n"}, "w1")),
            _text("done"),
            _tools(_tool("Bash", {"command": "python -c 'print(1)'"}, "b2")),
            _text("done"),
        ],
    )

    result = runner.run("implement a tiny change", execution_budget=_budget(turns=10, tools=15))

    assert result["transcript"]["acceptance_probe"]["acceptance_probe_status"] == "passed"
    events = result["transcript"].get("acceptance_probe_events", [])
    assert any(event["type"] == "stale" for event in events)
    assert any("acceptance probe stale" in str(m.get("content")).lower() for m in result["messages"])


def test_failed_probe_output_is_surfaced_into_next_agent_context(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            _tools(_tool("DefineAcceptanceProbe", {"commands": ["python -c 'import sys; print(\"bad\"); sys.exit(2)'"]}, "p1")),
            _tools(_tool("Bash", {"command": "python -c 'import sys; print(\"bad\"); sys.exit(2)'"}, "b1")),
            _tools(_tool("MarkAcceptanceProbeNotApplicable", {"reason": "synthetic test stops after observing failure context"}, "n1")),
            _text("done"),
        ],
    )

    result = runner.run("implement a tiny change", execution_budget=_budget())

    joined = "\n".join(str(m.get("content")) for m in result["messages"])
    assert "The acceptance probe failed" in joined
    assert "sys.exit(2)" in joined
    assert "bad" in joined


def test_initial_setup_may_perform_multiple_mutations_before_probe_first_runs(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            _tools(_tool("DefineAcceptanceProbe", {"commands": ["test -f a.txt && test -f b.txt"]}, "p1")),
            _tools(_tool("Write", {"file_path": "a.txt", "content": "a\n"}, "w1")),
            _tools(_tool("Write", {"file_path": "b.txt", "content": "b\n"}, "w2")),
            _tools(_tool("Bash", {"command": "test -f a.txt && test -f b.txt"}, "b1")),
            _text("done"),
        ],
    )

    result = runner.run("implement files", execution_budget=_budget(turns=10, tools=15))

    assert result["transcript"]["acceptance_probe"]["acceptance_probe_status"] == "passed"
    assert (tmp_path / "a.txt").exists() and (tmp_path / "b.txt").exists()


def test_not_applicable_requires_reason_and_allows_existing_completion(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            _tools(_tool("MarkAcceptanceProbeNotApplicable", {"reason": "documentation-only explanation has no local executable behavior"}, "n1")),
            _text("done"),
        ],
    )

    result = runner.run("explain the architecture", execution_budget=_budget())

    probe = result["transcript"]["acceptance_probe"]
    assert probe["acceptance_probe_status"] == "not_applicable"
    assert probe["probe_not_applicable_reason"]


def test_probe_events_and_final_status_written_to_artifacts(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            _tools(_tool("DefineAcceptanceProbe", {"commands": ["python -c 'print(1)'"]}, "p1")),
            _tools(_tool("Bash", {"command": "python -c 'print(1)'"}, "b1")),
            _text("done"),
        ],
    )

    result = runner.run("implement a tiny change", execution_budget=_budget())

    assert result["transcript_path"]
    saved = Path(result["transcript_path"]).read_text(encoding="utf-8")
    assert '"acceptance_probe"' in saved
    assert '"acceptance_probe_events"' in saved
    assert result["transcript"]["execution"]["acceptance_probe"]["acceptance_probe_status"] == "passed"


def test_synthetic_loop_fail_repair_rerun_pass_complete(tmp_path: Path) -> None:
    (tmp_path / "pkg.py").write_text("def answer():\n    return 0\n", encoding="utf-8")
    probe_cmd = "python -c 'import pkg; assert pkg.answer() == 42'"
    runner = _runner(
        tmp_path,
        [
            _tools(_tool("DefineAcceptanceProbe", {"commands": [probe_cmd], "description": "import pkg and validate answer"}, "p1")),
            _tools(_tool("Bash", {"command": probe_cmd}, "b1")),
            _tools(_tool("Write", {"file_path": "pkg.py", "content": "def answer():\n    return 42\n"}, "w1")),
            _tools(_tool("Bash", {"command": probe_cmd}, "b2")),
            _text("done"),
        ],
    )

    result = runner.run("fix pkg.answer", execution_budget=_budget(turns=10, tools=15))

    probe = result["transcript"]["acceptance_probe"]
    assert probe["acceptance_probe_status"] == "passed"
    assert probe["acceptance_probe_attempt_count"] == 2
    event_types = [event["type"] for event in result["transcript"]["acceptance_probe_events"]]
    assert event_types == ["defined", "failed", "stale", "passed"]
