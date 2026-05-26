from __future__ import annotations

from pathlib import Path

from villani_code.autonomous_stop import DoneReason, StopDecision, category_exhaustion_reason
from villani_code.execution import ExecutionBudget
from villani_code.state import Runner
from villani_code.task_contract import ObservableKind, RequiredObservable, TaskOutcomeContract


def test_stop_decision_is_typed_and_serializes_to_existing_value() -> None:
    assert StopDecision.BUDGET_EXHAUSTED == "budget_exhausted"
    assert StopDecision.parse("planner_churn") is StopDecision.PLANNER_CHURN


def test_done_reason_is_typed_and_serializes_to_existing_value() -> None:
    assert DoneReason.NO_OPPORTUNITIES == "No opportunities discovered."
    assert DoneReason.parse("Villani mode budget exhausted.") is DoneReason.BUDGET_EXHAUSTED


def test_category_exhaustion_reason_string_contract_is_stable() -> None:
    stop = category_exhaustion_reason({"tests": "attempted", "docs": "unknown", "entrypoints": "discovered"})
    assert stop.done_reason.startswith("No remaining opportunities above confidence threshold;")
    assert "tests examined: attempted" in stop.done_reason
    assert "docs examined: unknown" in stop.done_reason
    assert "entrypoints examined: discovered" in stop.done_reason


class _SequenceClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    def create_message(self, payload, stream):
        del payload, stream
        if self._idx >= len(self._responses):
            return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}
        response = self._responses[self._idx]
        self._idx += 1
        return response


def test_repeated_blocked_completion_terminates_contract_unsatisfied(tmp_path: Path, monkeypatch) -> None:
    events: list[dict] = []
    runner = Runner(
        client=_SequenceClient([{"role": "assistant", "content": [{"type": "text", "text": "done"}]}] * 3),
        repo=tmp_path,
        model="m",
        stream=False,
        small_model=True,
        event_callback=events.append,
    )
    monkeypatch.setattr(
        "villani_code.state.build_task_outcome_contract",
        lambda *args, **kwargs: TaskOutcomeContract(
            objective="Ensure required.txt exists.",
            task_mode="general",
            success_predicate="required file exists",
            required_observables=[RequiredObservable(kind=ObservableKind.FILE.value, path="required.txt", description="required")],
            behavioral_checks=[],
        ),
    )
    out = runner.run("Ensure required.txt exists.", execution_budget=ExecutionBudget(max_turns=5, max_tool_calls=5, max_seconds=30, max_no_edit_turns=5, max_reconsecutive_recon_turns=5))
    assert out["execution"]["terminated_reason"] == "contract_unsatisfied"
    assert any(event.get("type") == "completion_gate_blocked" for event in events)


def test_completion_gate_satisfied_event_emitted(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "required.txt").write_text("ok\n", encoding="utf-8")
    events: list[dict] = []
    runner = Runner(
        client=_SequenceClient([{"role": "assistant", "content": [{"type": "text", "text": "done"}]}]),
        repo=tmp_path,
        model="m",
        stream=False,
        small_model=True,
        event_callback=events.append,
    )
    monkeypatch.setattr(
        "villani_code.state.build_task_outcome_contract",
        lambda *args, **kwargs: TaskOutcomeContract(
            objective="Ensure required.txt exists.",
            task_mode="general",
            success_predicate="required file exists",
            required_observables=[RequiredObservable(kind=ObservableKind.FILE.value, path="required.txt", description="required")],
            behavioral_checks=[],
        ),
    )
    out = runner.run("Ensure required.txt exists.", execution_budget=ExecutionBudget(max_turns=3, max_tool_calls=3, max_seconds=30, max_no_edit_turns=5, max_reconsecutive_recon_turns=5))
    assert "response" in out
    assert any(event.get("type") == "completion_gate_satisfied" for event in events)
