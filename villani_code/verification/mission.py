from __future__ import annotations

from typing import Any

from villani_code.mission import MissionExecutionState, MissionOutcome, NodeStatus


def evaluate_mission_status(state: MissionExecutionState, max_no_progress: int = 3, max_no_activity: int = 3, budget_limit: int = 30) -> tuple[MissionOutcome | None, str]:
    mission = state.mission
    terminal = {NodeStatus.SUCCEEDED, NodeStatus.BLOCKED, NodeStatus.EXHAUSTED, NodeStatus.SKIPPED}
    if all(n.status in terminal for n in mission.nodes) and any(n.status == NodeStatus.SUCCEEDED for n in mission.nodes):
        return MissionOutcome.SOLVED, "All planned nodes completed with at least one success."
    if any(n.status == NodeStatus.BLOCKED for n in mission.nodes):
        return MissionOutcome.BLOCKED, "A mission node is blocked."
    if state.consecutive_no_progress >= max_no_progress:
        return MissionOutcome.STAGNATED, "Repeated no-progress cycles exceeded threshold."
    if state.consecutive_no_model_activity >= max_no_activity:
        return MissionOutcome.EXHAUSTED, "Repeated no-activity cycles exceeded threshold."
    total_attempts = sum(n.attempts for n in mission.nodes)
    if total_attempts >= budget_limit:
        return MissionOutcome.BUDGET_EXHAUSTED, "Mission budget exhausted."
    if any(n.failure_fingerprint and sum(1 for m in mission.nodes if m.failure_fingerprint == n.failure_fingerprint) >= 3 for n in mission.nodes):
        return MissionOutcome.EXHAUSTED, "Identical failure fingerprint repeated across nodes."
    if any("suspicious_breadth" in " ".join(n.evidence) for n in mission.nodes):
        return MissionOutcome.UNSAFE, "Suspicious patch breadth detected."
    return None, ""
