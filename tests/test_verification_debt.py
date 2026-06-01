from __future__ import annotations

import json
from pathlib import Path

from villani_code.debug_mode import build_debug_config
from villani_code.execution import ExecutionBudget
from villani_code.state import Runner
from villani_code.verification_debt import VerificationDebtState, classify_action, validation_result


class _SequenceClient:
    def __init__(self, responses: list[dict]):
        self._responses = responses
        self._idx = 0

    def create_message(self, _payload, stream=False):
        response = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return response


def _bash_result(command: str, exit_code: int = 0, stdout: str = "", stderr: str = "") -> dict:
    return {
        "content": json.dumps({"command": command, "exit_code": exit_code, "stdout": stdout, "stderr": stderr}),
        "is_error": False,
    }


def _record_write(state: VerificationDebtState, path: str) -> dict:
    return state.record_action("Write", {"file_path": path, "content": "x"}, {"content": f"Wrote {path}", "is_error": False})


def test_multiple_workspace_mutations_without_validation_accumulate_debt() -> None:
    state = VerificationDebtState()
    _record_write(state, "a.py")
    _record_write(state, "b.py")
    event = _record_write(state, "c.py")

    assert event["classification"] == "mutation"
    assert state.mutations_since_validation == 3
    assert state.verification_debt >= state.threshold


def test_reconnaissance_does_not_prematurely_trigger_debt_interventions() -> None:
    state = VerificationDebtState()
    for command in ["pwd", "git status --short", "rg 'foo' .", "cat README.md"]:
        event = state.record_action("Bash", {"command": command}, _bash_result(command))
        assert event["classification"] in {"reconnaissance", "administrative"}

    assert state.verification_debt == 0
    assert not state.should_intervene()


def test_meaningful_test_build_run_check_action_reduces_debt() -> None:
    state = VerificationDebtState()
    _record_write(state, "a.py")
    _record_write(state, "b.py")

    event = state.record_action("Bash", {"command": "pytest -q"}, _bash_result("pytest -q", exit_code=0, stdout="2 passed"))

    assert event["classification"] == "validation"
    assert event["validation_result"] == "useful_success"
    assert state.verification_debt == 0
    assert state.last_validation_result == "useful_success"


def test_useful_failed_validation_is_summarised_and_prioritised() -> None:
    state = VerificationDebtState()
    _record_write(state, "app.py")

    state.record_action(
        "Bash",
        {"command": "pytest -q"},
        _bash_result("pytest -q", exit_code=1, stderr="E AssertionError: expected 2 got 1"),
    )
    guidance = state.build_failed_validation_guidance()

    assert state.last_validation_result == "useful_failure"
    assert "pytest -q" in guidance
    assert "AssertionError" in guidance
    assert "smallest targeted change" in guidance


def test_threshold_crossing_debt_triggers_general_validation_intervention() -> None:
    state = VerificationDebtState()
    for idx in range(3):
        _record_write(state, f"file{idx}.py")

    intervention = state.build_validation_intervention() if state.should_intervene() else ""

    assert "most informative validation action" in intervention
    assert "test, build, import, executable invocation" in intervention
    assert "pytest" not in intervention.lower()


def test_initial_bootstrapping_is_permitted_before_validation_becomes_practical() -> None:
    state = VerificationDebtState(threshold=2, min_mutations_before_intervention=3)
    _record_write(state, "new_app.py")

    assert state.verification_debt > 0
    assert not state.should_intervene()


def test_successful_validation_does_not_guarantee_task_completion() -> None:
    state = VerificationDebtState()
    _record_write(state, "app.py")
    state.record_action("Bash", {"command": "python -c 'import app'"}, _bash_result("python -c 'import app'", exit_code=0))

    assert state.verification_debt == 0
    assert state.last_validation_result == "useful_success"
    assert not hasattr(state, "completed")


def test_completion_with_unresolved_verification_debt_is_permitted_but_recorded(tmp_path: Path) -> None:
    client = _SequenceClient(
        [
            {"id": "1", "role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "a.txt", "content": "a"}}]},
            {"id": "2", "role": "assistant", "content": [{"type": "tool_use", "id": "w2", "name": "Write", "input": {"file_path": "b.txt", "content": "b"}}]},
            {"id": "3", "role": "assistant", "content": [{"type": "tool_use", "id": "w3", "name": "Write", "input": {"file_path": "c.txt", "content": "c"}}]},
            {"id": "4", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
    )
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, plan_mode="off", auto_approve=True)

    result = runner.run("create files", execution_budget=ExecutionBudget(max_turns=6, max_tool_calls=5, max_seconds=10, max_no_edit_turns=5, max_reconsecutive_recon_turns=5))

    assert result["transcript"]["verification_debt"]["verification_debt"] >= result["transcript"]["verification_debt"]["threshold"]
    assert result["transcript"]["verification_debt"]["last_validation_result"] == "none"


def test_verification_telemetry_is_written_to_debug_artifacts(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    client = _SequenceClient(
        [
            {"id": "1", "role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "a.txt", "content": "a"}}]},
            {"id": "2", "role": "assistant", "content": [{"type": "tool_use", "id": "w2", "name": "Write", "input": {"file_path": "b.txt", "content": "b"}}]},
            {"id": "3", "role": "assistant", "content": [{"type": "tool_use", "id": "w3", "name": "Write", "input": {"file_path": "c.txt", "content": "c"}}]},
            {"id": "4", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
    )
    runner = Runner(
        client=client,
        repo=tmp_path,
        model="m",
        stream=False,
        plan_mode="off",
        auto_approve=True,
        debug_config=build_debug_config("trace", debug_root),
    )
    runner.run("create files", execution_budget=ExecutionBudget(max_turns=6, max_tool_calls=5, max_seconds=10, max_no_edit_turns=5, max_reconsecutive_recon_turns=5))

    run_dir = next(path for path in debug_root.iterdir() if path.is_dir())
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    assert any(event["event_type"] == "verification_action_classified" for event in events)
    assert any(event["event_type"] == "verification_debt_intervention" for event in events)
    assert any(event["event_type"] == "verification_debt_completion" for event in events)


def test_classifier_has_no_task_names_benchmark_identifiers_or_fixed_task_validation_commands() -> None:
    source = Path("villani_code/verification_debt.py").read_text(encoding="utf-8").lower()

    forbidden = ["terminal-bench", "terminal_bench", "task_id", "villani_bench", "bugfix_", "localize_"]
    assert not any(token in source for token in forbidden)
    assert classify_action("Bash", {"command": "pytest -q"}) == "validation"
    assert validation_result("Bash", {"command": "pip install missing"}, _bash_result("pip install missing", exit_code=1)) == "none"
