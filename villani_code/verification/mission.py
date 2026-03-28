from __future__ import annotations

from villani_code.mission import MissionExecutionState, MissionOutcome, NodeStatus


def evaluate_mission_status(state: MissionExecutionState, max_no_progress: int = 3, max_no_activity: int = 3, budget_limit: int = 30) -> tuple[MissionOutcome | None, str]:
    mission = state.mission
    terminal = {NodeStatus.SUCCEEDED, NodeStatus.BLOCKED, NodeStatus.EXHAUSTED, NodeStatus.SKIPPED}

    if any(n.status == NodeStatus.BLOCKED for n in mission.nodes):
        return MissionOutcome.BLOCKED, "A mission node is blocked."

    if state.consecutive_no_progress >= max_no_progress:
        return MissionOutcome.STAGNATED, "Repeated no-progress cycles exceeded threshold."
    if state.consecutive_no_model_activity >= max_no_activity:
        return MissionOutcome.EXHAUSTED, "Repeated no-activity cycles exceeded threshold."

    total_attempts = sum(n.attempts for n in mission.nodes)
    if total_attempts >= budget_limit:
        return MissionOutcome.BUDGET_EXHAUSTED, "Mission budget exhausted."

    fingerprints = [fp for fp in state.failure_fingerprint_history if fp]
    if fingerprints:
        repeated = max(fingerprints.count(fp) for fp in set(fingerprints))
        if repeated >= 3:
            return MissionOutcome.EXHAUSTED, "Identical failure fingerprint repeated across attempts."

    stale_localization = 0
    for idx in range(1, len(state.localization_history)):
        prev = state.localization_history[idx - 1]
        cur = state.localization_history[idx]
        if cur.target_files == prev.target_files and cur.confidence <= prev.confidence:
            stale_localization += 1
    if stale_localization >= 2:
        return MissionOutcome.STAGNATED, "Localization repeated without stronger evidence."

    if any("suspicious_breadth" in " ".join(n.evidence) for n in mission.nodes):
        return MissionOutcome.UNSAFE, "Suspicious patch breadth detected."

    if mission.nodes and all(n.status in terminal for n in mission.nodes):
        required = [n for n in mission.nodes if n.contract_type in {"validate", "contain_change", "narrow_fix", "broad_fix", "implement"}]
        required_ok = all(n.status == NodeStatus.SUCCEEDED for n in required) if required else any(n.status == NodeStatus.SUCCEEDED for n in mission.nodes)
        if required_ok:
            return MissionOutcome.SOLVED, "Mission graph completed with required contracts passing."
        return MissionOutcome.EXHAUSTED, "Mission graph completed without required contract success."

    return None, ""
