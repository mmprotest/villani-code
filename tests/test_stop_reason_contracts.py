from __future__ import annotations

from villani_code.autonomous_stop import DoneReason, StopDecision, category_exhaustion_reason


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
