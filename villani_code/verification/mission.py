from __future__ import annotations

from villani_code.mission import MissionExecutionState, MissionOutcome, reduce_normalized_mission_progress


def evaluate_mission_status(state: MissionExecutionState, max_no_progress: int = 3, max_no_activity: int = 3, budget_limit: int = 30) -> tuple[MissionOutcome | None, str]:
    mission = state.mission
    total_attempts = sum(n.attempts for n in mission.nodes)
    if total_attempts >= budget_limit:
        return MissionOutcome.BUDGET_EXHAUSTED, "Mission budget exhausted."
    normalized = reduce_normalized_mission_progress(state)
    if not normalized.node_outcomes:
        return None, ""
    if state.consecutive_no_progress >= max_no_progress and not normalized.deliverable_paths:
        return MissionOutcome.STAGNATED, "Repeated no-progress cycles exceeded threshold."
    if state.repeated_delta_states >= 3 and not normalized.deliverable_paths:
        return MissionOutcome.STAGNATED, "Repeated no/ambiguous delta outcomes exceeded threshold."
    if state.consecutive_no_model_activity >= max_no_activity and not normalized.deliverable_paths:
        return MissionOutcome.EXHAUSTED, "Repeated no-activity cycles exceeded threshold."
    if normalized.terminal_state == "success":
        return MissionOutcome.SOLVED, "Normalized mission reducer marked success."
    if normalized.terminal_state == "blocked":
        return MissionOutcome.BLOCKED, normalized.blocked_reason or "Normalized mission reducer marked blocked."
    if normalized.terminal_state == "partial_success":
        if normalized.validation_truth_status == "failed":
            return MissionOutcome.PARTIAL_SUCCESS_BUILT_VALIDATION_FAILED, "Deliverables exist but command validation failed."
        if mission.mission_type.value == "greenfield_build" and not state.scratchpad.has_runnable_entrypoint:
            return MissionOutcome.PARTIAL_SUCCESS_SCAFFOLD_ONLY, "Scaffold exists but runnable entrypoint is incomplete."
        return MissionOutcome.PARTIAL_SUCCESS_BUILT_UNVALIDATED, "Deliverables exist but validation is unproven."
    if normalized.terminal_state == "failed":
        return MissionOutcome.EXHAUSTED, "Normalized mission reducer marked failed."
    if normalized.terminal_state == "stagnated":
        return MissionOutcome.STAGNATED, "Normalized mission reducer marked stagnated."
    return None, ""
