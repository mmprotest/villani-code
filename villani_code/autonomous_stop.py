from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from villani_code.mission import MissionExecutionState, MissionOutcome


class StopDecision(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_OPPORTUNITIES = "no_opportunities"
    BELOW_THRESHOLD = "below_threshold"
    PLANNER_CHURN = "planner_churn"
    STAGNATION = "stagnation"


class DoneReason(StrEnum):
    NO_OPPORTUNITIES = "No opportunities discovered."
    PLANNER_CHURN = "Stopped: planner loop with no model activity."
    BUDGET_EXHAUSTED = "Villani mode budget exhausted."


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


def evaluate_mission_stop(
    state: MissionExecutionState,
    *,
    max_no_progress: int = 3,
    max_no_action: int = 3,
    budget_limit: int = 30,
) -> tuple[MissionOutcome | None, str]:
    total_attempts = sum(n.attempts for n in state.mission.nodes)
    if total_attempts >= budget_limit:
        return MissionOutcome.BUDGET_EXHAUSTED, "Mission execution budget exhausted."
    if state.consecutive_no_progress >= max_no_progress:
        return MissionOutcome.STAGNATED, "No-progress cycle threshold exceeded."
    if state.consecutive_no_model_activity >= max_no_action:
        return MissionOutcome.EXHAUSTED, "No-action cycle threshold exceeded."

    fingerprints = [n.failure_fingerprint for n in state.mission.nodes if n.failure_fingerprint]
    if fingerprints:
        repeated = max(fingerprints.count(fp) for fp in set(fingerprints))
        if repeated >= 3:
            return MissionOutcome.EXHAUSTED, "Repeated identical failure fingerprint."

    blocked = [n for n in state.mission.nodes if n.status.value == "blocked"]
    if blocked:
        return MissionOutcome.BLOCKED, blocked[0].blockers[0] if blocked[0].blockers else "Node blocked."

    unsafe = [n for n in state.mission.nodes if any("suspicious_breadth" in e for e in n.evidence)]
    if unsafe:
        return MissionOutcome.UNSAFE, "Unsafe patch breadth detected."

    terminal = {"succeeded", "blocked", "exhausted", "skipped"}
    if state.mission.nodes and all(n.status.value in terminal for n in state.mission.nodes):
        if any(n.status.value == "succeeded" for n in state.mission.nodes):
            return MissionOutcome.SOLVED, "Mission graph completed."
        return MissionOutcome.EXHAUSTED, "Mission graph terminated without successful nodes."

    return None, ""
