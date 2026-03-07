from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StopDecision(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_OPPORTUNITIES = "no_opportunities"
    BELOW_THRESHOLD = "below_threshold"
    PLANNER_CHURN = "planner_churn"
    STAGNATION = "stagnation"

    @classmethod
    def parse(cls, value: str) -> "StopDecision":
        return cls(value)


class DoneReason(StrEnum):
    NO_OPPORTUNITIES = "No opportunities discovered."
    PLANNER_CHURN = "Stopped: planner loop with no model activity."
    BUDGET_EXHAUSTED = "Villani mode budget exhausted."

    @classmethod
    def parse(cls, value: str) -> "DoneReason":
        return cls(value)


@dataclass(slots=True)
class CategoryStopReason:
    rationale: dict[str, str]
    done_reason: str


def category_exhaustion_reason(category_state: dict[str, str]) -> CategoryStopReason:
    rationale = {
        "tests": category_state.get("tests", "unknown"),
        "docs": category_state.get("docs", "unknown"),
        "entrypoints": category_state.get("entrypoints", "unknown"),
        "improvements": "exhausted",
    }
    done_reason = (
        "No remaining opportunities above confidence threshold; "
        f"tests examined: {rationale['tests']}; "
        f"docs examined: {rationale['docs']}; "
        f"entrypoints examined: {rationale['entrypoints']}."
    )
    return CategoryStopReason(rationale=rationale, done_reason=done_reason)
