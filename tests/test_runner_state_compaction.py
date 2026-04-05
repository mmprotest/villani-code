from __future__ import annotations

import json
from pathlib import Path

from villani_code.state import Runner
from villani_code.state_runtime import prepare_messages_for_model
from villani_code.summarizer import summarize_mission_state


class DummyClient:
    def create_message(self, _payload, stream=False):
        return {"content": [{"type": "text", "text": "ok"}]}


def _seed_repo(repo: Path) -> None:
    (repo / "villani_code").mkdir(parents=True, exist_ok=True)
    (repo / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


def test_run_state_populates_from_tool_results_and_promotes_verified_facts(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("state")

    bash_ok = {
        "is_error": False,
        "content": json.dumps({"command": "python -m pytest -q", "exit_code": 0, "stdout": "", "stderr": ""}),
    }
    runner._update_run_state_from_tool_result("Bash", {"command": "python -m pytest -q"}, bash_ok)

    assert runner._mission_state is not None
    assert runner._mission_state.working_interpreter_cmd == "python"
    assert any(f.kind == "interpreter" for f in runner._mission_state.verified_facts)
    assert any("Interpreter command works" in fact for fact in runner._mission_state.environment_facts)


def test_duplicate_validation_detection_compacts_output_and_state(tmp_path: Path, monkeypatch) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._ensure_mission("state")
    runner._verification_baseline_changed = set()

    from villani_code import state_runtime

    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: [])
    runner._run_verification("edit")
    runner._run_verification("edit")
    third = runner._run_verification("edit")

    assert "status: redundant" in third
    assert "verification state repeated" not in third
    assert runner._mission_state is not None
    assert runner._mission_state.validation_fingerprint
    assert "redundant validation" in runner._mission_state.last_validation_summary


def test_duplicate_failed_action_detection_with_unchanged_evidence(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("state")

    result = {"is_error": True, "content": "command failed: same"}
    runner._update_run_state_from_tool_result("Bash", {"command": "python -m pytest"}, result)
    runner._update_run_state_from_tool_result("Bash", {"command": "python -m pytest"}, result)

    assert runner._mission_state is not None
    assert any("Avoid repeating unchanged failed action" in s for s in runner._mission_state.attempted_strategies)


def test_artifact_lineage_transitions_active_to_superseded(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("state")

    runner._track_artifact_lineage("Write", {"file_path": "a.py"})
    runner._track_artifact_lineage("Write", {"file_path": "b.py"})

    assert runner._mission_state is not None
    assert runner._mission_state.active_artifacts[0] == "b.py"
    assert "a.py" in runner._mission_state.superseded_artifacts


def test_prepare_messages_compacts_verification_boilerplate_and_stale_tool_results(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("state")
    noisy = "verification state repeated\nno new evidence was produced\nnext step must either change target, change validation evidence, or stop\n" + ("x" * 800)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "task"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "echo"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": noisy}]},
    ]

    prepared = prepare_messages_for_model(runner, messages)
    final_content = str(prepared[-1]["content"][0]["content"])
    assert "verification state repeated" not in final_content
    assert "no new evidence was produced" not in final_content


def test_discovered_environment_facts_reused_in_mission_summary(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("state")
    runner._update_run_state_from_tool_result(
        "Bash",
        {"command": "python -m pytest -q"},
        {"is_error": False, "content": json.dumps({"command": "python -m pytest -q", "exit_code": 0, "stdout": "", "stderr": ""})},
    )

    assert runner._mission_state is not None
    summary = summarize_mission_state(runner._mission_state)
    assert "Verified facts:" in summary
    assert "interpreter=python" in summary


def test_noisy_multi_step_regression_context_growth_is_bounded(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("state")

    messages = [{"role": "user", "content": [{"type": "text", "text": "start"}]}]
    for idx in range(12):
        tool_id = f"toolu_{idx}"
        messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": "echo"}}]})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": "verification state repeated\nno new evidence was produced\n" + ("0123456789" * 120),
                    }
                ],
            }
        )

    raw_chars = sum(len(str(m.get("content", ""))) for m in messages)
    prepared = prepare_messages_for_model(runner, messages)
    prepared_chars = sum(len(str(m.get("content", ""))) for m in prepared)

    assert prepared_chars < raw_chars
    assert prepared_chars <= int(raw_chars * 0.7)
    prepared_blob = "\n".join(str(m.get("content", "")) for m in prepared)
    assert "verification state repeated" not in prepared_blob


def test_helper_artifact_not_deleted_by_default(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    helper = tmp_path / "scratch_helper.txt"
    helper.write_text("keep", encoding="utf-8")
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("state")
    runner._track_artifact_lineage("Write", {"file_path": "scratch_helper.txt"})

    assert helper.exists()
    assert runner._mission_state is not None
    assert "scratch_helper.txt" in runner._mission_state.helper_artifacts
