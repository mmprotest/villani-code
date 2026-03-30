from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from villani_code.mission import MissionExecutionState, MissionNode
from villani_code.mission_planner import MissionPlanner


@dataclass(slots=True)
class RecoveryDecision:
    strategy: str
    reason: str
    nodes: list[MissionNode] = field(default_factory=list)
    mark_blocked: bool = False
    mark_exhausted: bool = False


class RecoveryPlanner:
    def __init__(self, planner: MissionPlanner):
        self.planner = planner

    def plan_recovery(
        self,
        mission_state: MissionExecutionState,
        node: MissionNode,
        node_outcome: dict[str, Any],
    ) -> RecoveryDecision:
        changed_files = list(node_outcome.get("changed_files", []) or [])
        user_changed_files = list(node_outcome.get("user_space_changed_files", []) or [])
        no_changes = not user_changed_files
        worsened = bool(node_outcome.get("validation_worsened"))
        no_improvement = bool(node_outcome.get("patch_no_improvement"))
        repeated_failure = bool(node_outcome.get("same_failure_repeated"))
        too_broad = bool(node_outcome.get("suspicious_breadth"))
        tool_denied = bool(node_outcome.get("tool_denied"))
        prose_only = bool(node_outcome.get("prose_only"))
        clarification_requested = bool(node_outcome.get("clarification_requested"))
        internal_only_patch = bool(node_outcome.get("internal_only_patch"))
        docs_only_user_space = bool(node_outcome.get("docs_only_user_space"))
        weak_localization = bool(node_outcome.get("localization_weak"))
        stale_localization = bool(node_outcome.get("localization_stale"))
        delta = str(node_outcome.get("delta_classification", "ambiguous"))
        delta_reason = str(node_outcome.get("delta_reason", ""))
        localization_improved = bool(node_outcome.get("delta_details", {}).get("sharper_localization"))
        repeated_delta_state = mission_state.repeated_delta_states >= 2
        is_greenfield = mission_state.mission.mission_type.value == "greenfield_build"
        greenfield_progress = dict(mission_state.greenfield_progress or {})
        scaffold_succeeded = bool(greenfield_progress.get("successful_greenfield_scaffold"))
        sticky_direction = str(node_outcome.get("authoritative_direction", "") or mission_state.scratchpad.chosen_project_direction).strip()
        repair_focus = str(node_outcome.get("repair_focus", "")).strip()

        if is_greenfield:
            if scaffold_succeeded and node.phase.value in {"inspect_workspace", "choose_project_direction", "scaffold_project"}:
                return RecoveryDecision("advance_after_scaffold", "Authoritative greenfield scaffold success already captured; continue forward without recovery branch.")
            if tool_denied:
                return RecoveryDecision("blocked", "Tooling or permission denial encountered.", mark_blocked=True)
            if clarification_requested:
                strategy = "rescope" if scaffold_succeeded else "force_scaffold"
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, strategy, "Autonomous node asked for confirmation instead of acting")
                return RecoveryDecision("force_action", "Confirmation/clarification prompts are invalid in autonomous greenfield mode.", nodes=nodes)
            if repeated_failure:
                strategy = "rescope" if scaffold_succeeded else "simplify_direction"
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, strategy, "Repeated greenfield failure fingerprint")
                if sticky_direction:
                    for n in nodes:
                        n.evidence.append(f"sticky_direction:{sticky_direction}")
                return RecoveryDecision(strategy, "Repeated failures; switch to simpler viable slice.", nodes=nodes)
            if too_broad:
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "rescope", "Greenfield scope too broad")
                return RecoveryDecision("rescope", "Reduce scope to a smaller vertical slice.", nodes=nodes)
            if internal_only_patch:
                strategy = "rescope" if scaffold_succeeded else "force_scaffold"
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, strategy, "Only internal artifacts were changed")
                return RecoveryDecision(strategy, "Internal artifacts do not count; force user-space deliverable progress.", nodes=nodes)
            if docs_only_user_space:
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "rescope", "Docs-only output in open-ended greenfield build")
                return RecoveryDecision("rescope", "Docs-only output is insufficient; force runnable vertical slice.", nodes=nodes)
            if no_changes or prose_only:
                if scaffold_succeeded:
                    strategy = "rescope"
                else:
                    strategy = "force_scaffold" if node.phase.value in {"inspect_workspace", "scaffold_project"} else "simplify_direction"
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, strategy, "No concrete build progress")
                if sticky_direction:
                    for n in nodes:
                        n.evidence.append(f"sticky_direction:{sticky_direction}")
                return RecoveryDecision(strategy, "No effectful creation progress; force concrete user-space creation.", nodes=nodes)
            if no_improvement or worsened or delta == "regression":
                reason = repair_focus or "Build validation did not improve"
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "rescope", reason)
                return RecoveryDecision("rescope", "Validation/setup failed; pivot to a simpler viable slice.", nodes=nodes)
            if repeated_delta_state:
                strategy = "rescope" if scaffold_succeeded else "simplify_direction"
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, strategy, "Repeated ambiguous delta")
                return RecoveryDecision(strategy, "Repeated low-delta outcomes; pick a simpler direction.", nodes=nodes)

        if tool_denied:
            return RecoveryDecision("blocked", "Tooling or permission denial encountered.", mark_blocked=True)
        if repeated_failure:
            if node.phase.value == "validate":
                nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "broaden", "Repeated validation fingerprint")
                return RecoveryDecision("branch_broaden", "Same failure repeated; branch away from same validation loop.", nodes=nodes)
            return RecoveryDecision("exhausted", "Repeated same failure fingerprint without delta.", mark_exhausted=True)
        if too_broad:
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Patch breadth too broad")
            return RecoveryDecision("retry_narrow", "Large blast radius detected.", nodes=nodes)
        if delta == "regression":
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Regression after node execution")
            return RecoveryDecision("rollback_direction", f"Delta indicates regression ({delta_reason}); retry with narrower strategy.", nodes=nodes)
        if worsened and changed_files:
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Validation worsened after patch")
            return RecoveryDecision("repair_repair", "Patch worsened state; execute repair-of-repair.", nodes=nodes)
        if no_improvement and changed_files:
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "broaden", "Patch had no measurable improvement")
            return RecoveryDecision("alternate_strategy", "Patch changed files but no measurable improvement; change strategy.", nodes=nodes)
        if localization_improved and no_improvement:
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Localization improved; focus repair")
            return RecoveryDecision("focused_repair", "Localization sharpened despite failed node; retry with tighter file focus.", nodes=nodes)
        if repeated_delta_state and (stale_localization or weak_localization):
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "broaden", "Repeated identical low-delta state")
            return RecoveryDecision("force_broaden", "Repeated no/ambiguous improvement; force broader evidence collection.", nodes=nodes)
        if no_changes:
            strategy = "relocalize" if (weak_localization or stale_localization) else "broaden"
            reason = "No file changes produced and localization weak/stale" if strategy == "relocalize" else "No file changes produced"
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, strategy, reason)
            return RecoveryDecision(strategy, "No effectful progress; re-branch.", nodes=nodes)
        if prose_only:
            if mission_state.consecutive_no_model_activity >= 2:
                return RecoveryDecision("exhausted", "Repeated prose-only cycles.", mark_exhausted=True)
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Force constrained action")
            return RecoveryDecision("force_action", "Model produced no concrete progress.", nodes=nodes)

        nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Generic recovery")
        return RecoveryDecision("generic", "Fallback recovery branch.", nodes=nodes)
