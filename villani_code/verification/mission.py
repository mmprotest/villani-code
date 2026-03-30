from __future__ import annotations

from villani_code.mission import MissionExecutionState, MissionOutcome, NodeStatus

INTERNAL_ARTIFACT_PREFIXES = (".villani/", ".villani_code/")


def _is_user_space_path(path: str) -> bool:
    return not str(path).startswith(INTERNAL_ARTIFACT_PREFIXES)


def evaluate_mission_status(state: MissionExecutionState, max_no_progress: int = 3, max_no_activity: int = 3, budget_limit: int = 30) -> tuple[MissionOutcome | None, str]:
    mission = state.mission
    terminal = {NodeStatus.SUCCEEDED, NodeStatus.BLOCKED, NodeStatus.EXHAUSTED, NodeStatus.SKIPPED}

    if any(n.status == NodeStatus.BLOCKED for n in mission.nodes):
        return MissionOutcome.BLOCKED, "A mission node is blocked."

    greenfield_gate_open = False
    greenfield_partial_outcome: tuple[MissionOutcome, str] | None = None
    if mission.mission_type.value == "greenfield_build":
        progress = dict(state.greenfield_progress or {})
        persisted_deliverables = [str(x) for x in list(progress.get("deliverable_paths", []) or []) if _is_user_space_path(str(x))]
        validate_entries = [
            item
            for item in state.verification_history
            if str(item.get("node_phase", "")) == "validate_project"
        ]
        has_validation_evidence = any(
            str(item.get("node_phase", "")) == "validate_project"
            and str(item.get("validation_evidence_kind", "")) == "real_command_results"
            for item in state.verification_history
        )
        validation_attempted = bool(validate_entries)
        validation_failed = any(
            int((item.get("validation_summary", {}) or {}).get("failed", 0) or 0) > 0
            for item in validate_entries
        )
        unresolved_critical = bool(progress.get("unresolved_critical_contract_violation", False))
        greenfield_gate_open = bool(persisted_deliverables) and bool(state.scratchpad.has_runnable_entrypoint) and has_validation_evidence and not unresolved_critical
        if persisted_deliverables and state.scratchpad.has_runnable_entrypoint and not unresolved_critical:
            if validation_attempted and validation_failed:
                greenfield_partial_outcome = (
                    MissionOutcome.PARTIAL_SUCCESS_BUILT_VALIDATION_FAILED,
                    "Built artifact exists and validation evidence shows failures.",
                )
            elif validation_attempted and not has_validation_evidence:
                greenfield_partial_outcome = (
                    MissionOutcome.PARTIAL_SUCCESS_BUILT_UNVALIDATED,
                    "Built artifact exists but validation commands were not captured as authoritative evidence.",
                )
            elif not validation_attempted:
                greenfield_partial_outcome = (
                    MissionOutcome.PARTIAL_SUCCESS_BUILT_UNVALIDATED,
                    "Built artifact exists but validation is unproven.",
                )
        elif persisted_deliverables and not state.scratchpad.has_runnable_entrypoint and not unresolved_critical:
            greenfield_partial_outcome = (
                MissionOutcome.PARTIAL_SUCCESS_SCAFFOLD_ONLY,
                "Greenfield scaffold exists but runnable artifact is incomplete.",
            )
        summaries = [n for n in mission.nodes if n.phase.value == "summarize_outcome"]
        summary_complete = bool(summaries) and all(n.status in {NodeStatus.SUCCEEDED, NodeStatus.SKIPPED, NodeStatus.EXHAUSTED} for n in summaries)
        if greenfield_partial_outcome is not None and summary_complete:
            return greenfield_partial_outcome

    if state.consecutive_no_progress >= max_no_progress and not greenfield_gate_open:
        if greenfield_partial_outcome is not None:
            return greenfield_partial_outcome
        return MissionOutcome.STAGNATED, "Repeated no-progress cycles exceeded threshold."
    if state.repeated_delta_states >= 3 and not greenfield_gate_open:
        if greenfield_partial_outcome is not None:
            return greenfield_partial_outcome
        return MissionOutcome.STAGNATED, "Repeated no/ambiguous delta outcomes exceeded threshold."
    if state.consecutive_no_model_activity >= max_no_activity and not greenfield_gate_open:
        if greenfield_partial_outcome is not None:
            return greenfield_partial_outcome
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
        if greenfield_partial_outcome is not None:
            return greenfield_partial_outcome
        return MissionOutcome.STAGNATED, "Localization repeated without stronger evidence."

    if any("suspicious_breadth" in " ".join(n.evidence) for n in mission.nodes):
        return MissionOutcome.UNSAFE, "Suspicious patch breadth detected."

    if greenfield_gate_open:
        summaries = [n for n in mission.nodes if n.phase.value == "summarize_outcome"]
        if summaries and all(n.status == NodeStatus.SUCCEEDED for n in summaries):
            return MissionOutcome.SOLVED, "Greenfield completion gate satisfied and outcome summarized."
    if mission.mission_type.value == "greenfield_build":
        summaries = [n for n in mission.nodes if n.phase.value == "summarize_outcome"]
        if greenfield_partial_outcome is not None and summaries and all(n.status in {NodeStatus.SUCCEEDED, NodeStatus.SKIPPED, NodeStatus.EXHAUSTED} for n in summaries):
            return greenfield_partial_outcome

    if mission.nodes and all(n.status in terminal for n in mission.nodes):
        if mission.mission_type.value == "greenfield_build":
            if state.scratchpad.mission_type == "greenfield_build" and mission.mission_type.value != state.scratchpad.mission_type:
                return MissionOutcome.STAGNATED, "Mission type regressed against authoritative scratchpad."
            greenfield_progress = dict(state.greenfield_progress or {})
            persisted_deliverables = [str(x) for x in list(greenfield_progress.get("deliverable_paths", []) or []) if _is_user_space_path(str(x))]
            scaffold_success = bool(greenfield_progress.get("successful_greenfield_scaffold"))
            deliverable_nodes = [n for n in mission.nodes if n.phase.value in {"scaffold_project", "implement_vertical_slice"}]
            deliverables_ok = all(n.status == NodeStatus.SUCCEEDED for n in deliverable_nodes) and (
                bool(persisted_deliverables)
                or any(any(_is_user_space_path(p) for p in n.last_outcome.changed_files) for n in deliverable_nodes)
            )
            scaffold_nodes = [n for n in mission.nodes if n.phase.value == "scaffold_project"]
            early_scaffold_ok = scaffold_success or (
                bool(scaffold_nodes) and all(
                    n.status == NodeStatus.SUCCEEDED and any(_is_user_space_path(p) for p in n.last_outcome.changed_files) for n in scaffold_nodes
                )
            )
            validate_nodes = [n for n in mission.nodes if n.phase.value == "validate_project"]
            summaries = [n for n in mission.nodes if n.phase.value == "summarize_outcome"]
            if early_scaffold_ok and deliverables_ok and all(n.status == NodeStatus.SUCCEEDED for n in validate_nodes + summaries):
                return MissionOutcome.SOLVED, "Greenfield mission completed with user-space runnable deliverable evidence."
            if greenfield_partial_outcome is not None:
                return greenfield_partial_outcome
            return MissionOutcome.EXHAUSTED, "Greenfield graph finished without required early user-space scaffold + deliverable + validation evidence."
        required = [n for n in mission.nodes if n.contract_type in {"validate", "contain_change", "narrow_fix", "broad_fix", "implement"}]
        required_ok = all(n.status == NodeStatus.SUCCEEDED for n in required) if required else any(n.status == NodeStatus.SUCCEEDED for n in mission.nodes)
        if required_ok:
            return MissionOutcome.SOLVED, "Mission graph completed with required contracts passing."
        return MissionOutcome.EXHAUSTED, "Mission graph completed without required contract success."

    return None, ""
