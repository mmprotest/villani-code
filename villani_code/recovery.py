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
        no_changes = not bool(node_outcome.get("changed_files"))
        worsened = bool(node_outcome.get("validation_worsened"))
        repeated_failure = bool(node_outcome.get("same_failure_repeated"))
        too_broad = bool(node_outcome.get("suspicious_breadth"))
        tool_denied = bool(node_outcome.get("tool_denied"))
        prose_only = bool(node_outcome.get("prose_only"))
        weak_localization = bool(node_outcome.get("localization_weak"))

        if tool_denied:
            return RecoveryDecision("blocked", "Tooling or permission denial encountered.", mark_blocked=True)
        if repeated_failure:
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "broaden", "Repeated failure fingerprint")
            return RecoveryDecision("branch_broaden", "Same failure repeated; branching strategy.", nodes=nodes)
        if too_broad:
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Patch breadth too broad")
            return RecoveryDecision("retry_narrow", "Large blast radius detected.", nodes=nodes)
        if worsened:
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Validation worsened after patch")
            return RecoveryDecision("repair_repair", "Repair-of-repair needed.", nodes=nodes)
        if no_changes:
            strategy = "relocalize" if weak_localization else "broaden"
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, strategy, "No file changes produced")
            return RecoveryDecision(strategy, "No effectful progress; re-branch.", nodes=nodes)
        if prose_only:
            if mission_state.consecutive_no_model_activity >= 2:
                return RecoveryDecision("exhausted", "Repeated prose-only cycles.", mark_exhausted=True)
            nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Force constrained action")
            return RecoveryDecision("force_action", "Model produced no concrete progress.", nodes=nodes)

        nodes = self.planner.spawn_recovery_nodes(mission_state.mission, node, "narrow", "Generic recovery")
        return RecoveryDecision("generic", "Fallback recovery branch.", nodes=nodes)
