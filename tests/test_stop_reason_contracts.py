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


from types import SimpleNamespace

from villani_code import state_runtime
from villani_code.feedback_interpreter import interpret_feedback
from villani_code.progress_ledger import ProgressLedger, format_recovery_packet
from villani_code.task_contract import check_contract_satisfaction


def test_reliability_event_traceability_packet_can_capture_all_stage_events(tmp_path: Path) -> None:
    events: list[dict] = []
    contract = TaskOutcomeContract(
        objective="write artifacts/out.txt",
        task_mode="general",
        success_predicate="artifact exists",
        required_observables=[
            RequiredObservable(kind=ObservableKind.FILE.value, path="artifacts/out.txt", description="output")
        ],
    )

    events.append({"type": "contract_created", "objective": contract.objective})
    contract_result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    events.append({"type": "contract_satisfaction_checked", "satisfied": contract_result.satisfied})

    ledger = ProgressLedger()
    ledger.record_observation(
        tool_name="Patch",
        tool_input={"file_path": "src/app.py"},
        result_is_error=False,
        changed_files=["src/app.py"],
        validation_artifacts=[],
        verification_fingerprint="same",
        contract_satisfied=contract_result.satisfied,
        contract_findings_count=len(contract_result.findings),
    )
    assessment = ledger.assess()
    events.append({"type": "progress_assessed", "stalled": assessment.stalled})

    packet = format_recovery_packet(assessment, contract, "", ["src/app.py"])
    events.append({"type": "recovery_packet_injected", "has_packet": "<recovery_packet>" in packet})

    gate = state_runtime.evaluate_completion_gate(
        SimpleNamespace(repo=tmp_path, _task_outcome_contract=contract, _last_inspection_summary="", _inspection_summary=""),
        changed_files=[],
        validation_artifacts=[],
    )
    events.append({"type": "completion_gate_blocked" if not gate["allowed"] else "completion_gate_satisfied"})

    interpretation = interpret_feedback(
        command_results=[{"command": "pytest -q", "exit": 1, "stdout": "", "stderr": "Traceback\n  File \"src/app.py\", line 1"}],
        contract_result=contract_result,
        changed_files=["src/app.py"],
    )
    events.append({"type": "feedback_interpretation_created", "action": interpretation.likely_next_action})

    types = {event["type"] for event in events}
    assert "contract_created" in types
    assert "contract_satisfaction_checked" in types
    assert "progress_assessed" in types
    assert "recovery_packet_injected" in types
    assert "completion_gate_blocked" in types
    assert "feedback_interpretation_created" in types
