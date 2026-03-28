from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from villani_code.autonomy import TaskContract
from villani_code.mission import Mission, MissionNode, MissionType, NodePhase, NodeStatus


class MissionPlanner:
    def build_mission(self, objective: str, repo_root: str, repo_signals: dict[str, Any] | None = None) -> Mission:
        mission_type = self.classify_mission_type(objective)
        mission = Mission(
            mission_id=f"mission-{uuid.uuid4().hex[:10]}",
            user_goal=objective.strip() or "maintenance patrol and stabilization",
            mission_type=mission_type,
            success_criteria=self.derive_success_criteria(objective, mission_type),
            repo_root=repo_root,
        )
        mission.nodes = self.build_initial_nodes(mission, repo_signals=repo_signals)
        return mission

    def classify_mission_type(self, objective: str) -> MissionType:
        low = (objective or "").lower()
        if not low.strip():
            return MissionType.MAINTENANCE
        if any(k in low for k in ["contain", "regression", "fallout", "blast radius", "diff"]):
            return MissionType.REGRESSION_CONTAINMENT
        if any(k in low for k in ["feature", "add", "implement", "support"]):
            return MissionType.FEATURE
        if any(k in low for k in ["validate", "verify", "test only"]):
            return MissionType.VALIDATION_ONLY
        if any(k in low for k in ["refactor", "rename", "cleanup"]):
            return MissionType.NARROW_REFACTOR
        if any(k in low for k in ["stabilize", "hardening", "reliability"]):
            return MissionType.REPO_STABILIZATION
        return MissionType.BUGFIX

    def derive_success_criteria(self, objective: str, mission_type: MissionType) -> list[str]:
        base = ["Produce evidence-backed progress.", "Run targeted validation after each effectful change."]
        if mission_type == MissionType.FEATURE:
            return base + ["Feature behavior implemented.", "No obvious regressions in impacted area."]
        if mission_type == MissionType.REGRESSION_CONTAINMENT:
            return base + ["Containment checks pass for changed surface.", "Blast radius validation is complete."]
        if mission_type == MissionType.MAINTENANCE:
            return base + ["At least one high-confidence maintenance improvement completed."]
        if mission_type == MissionType.VALIDATION_ONLY:
            return ["Validation completed and summarized with failures localized."]
        return base + ["Original defect is no longer reproducible."]

    def build_initial_nodes(self, mission: Mission, repo_signals: dict[str, Any] | None = None) -> list[MissionNode]:
        repo_signals = repo_signals or {}
        validation = list(repo_signals.get("likely_validation_commands", []) or [])
        nodes: list[MissionNode] = []
        def _n(suffix: str, title: str, phase: NodePhase, contract: str, objective: str, deps: list[str] | None = None) -> MissionNode:
            return MissionNode(
                node_id=f"{mission.mission_id}-{suffix}",
                title=title,
                phase=phase,
                objective=objective,
                contract_type=contract,
                validation_commands=list(validation[:3]),
                depends_on=list(deps or []),
                status=NodeStatus.READY if not deps else NodeStatus.PENDING,
            )

        if mission.mission_type in {MissionType.BUGFIX, MissionType.REPO_STABILIZATION, MissionType.NARROW_REFACTOR}:
            n1 = _n("localize", "Localize likely fault", NodePhase.LOCALIZE, TaskContract.LOCALIZE.value, mission.user_goal)
            n2 = _n("repro", "Reproduce failure", NodePhase.REPRODUCE, TaskContract.REPRODUCE.value, "Reproduce and capture failing signal", [n1.node_id])
            n3 = _n("fix", "Apply minimal fix", NodePhase.NARROW_FIX, TaskContract.NARROW_FIX.value, "Implement smallest safe change", [n2.node_id])
            n4 = _n("validate", "Validate fix", NodePhase.VALIDATE, TaskContract.VALIDATE.value, "Run targeted validation", [n3.node_id])
            nodes = [n1, n2, n3, n4]
        elif mission.mission_type == MissionType.FEATURE:
            n1 = _n("inspect", "Inspect current implementation", NodePhase.INSPECT, TaskContract.INSPECT.value, mission.user_goal)
            n2 = _n("impl", "Implement feature", NodePhase.BROAD_FIX, TaskContract.IMPLEMENT.value, "Implement objective and related tests", [n1.node_id])
            n3 = _n("validate", "Validate feature", NodePhase.VALIDATE, TaskContract.VALIDATE.value, "Run feature-focused validation", [n2.node_id])
            nodes = [n1, n2, n3]
        elif mission.mission_type == MissionType.REGRESSION_CONTAINMENT:
            n1 = _n("diff", "Localize change impact", NodePhase.LOCALIZE, TaskContract.CONTAIN_CHANGE.value, "Analyze diff and likely blast radius")
            n2 = _n("contain", "Contain regression risk", NodePhase.VALIDATE, TaskContract.CONTAIN_CHANGE.value, "Execute targeted impacted validations", [n1.node_id])
            nodes = [n1, n2]
        elif mission.mission_type == MissionType.VALIDATION_ONLY:
            n1 = _n("validate", "Run validation", NodePhase.VALIDATE, TaskContract.VALIDATE.value, mission.user_goal)
            nodes = [n1]
        else:  # maintenance
            n1 = _n("inspect", "Inspect repository health", NodePhase.INSPECT, TaskContract.INSPECT.value, "Find highest-leverage maintenance opportunity")
            n2 = _n("cleanup", "Apply narrow maintenance change", NodePhase.NARROW_FIX, TaskContract.CLEANUP.value, "Apply one focused maintenance improvement", [n1.node_id])
            n3 = _n("validate", "Validate maintenance change", NodePhase.VALIDATE, TaskContract.VALIDATE.value, "Run targeted validation", [n2.node_id])
            nodes = [n1, n2, n3]

        return nodes

    def spawn_recovery_nodes(self, mission: Mission, failed_node: MissionNode, strategy: str, reason: str) -> list[MissionNode]:
        base_id = f"{mission.mission_id}-recovery-{uuid.uuid4().hex[:6]}"
        if strategy == "broaden":
            node = MissionNode(base_id, "Broaden inspection", NodePhase.INSPECT, f"Recover from: {reason}", TaskContract.INSPECT.value, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)
            return [node]
        if strategy == "relocalize":
            node = MissionNode(base_id, "Re-localize root cause", NodePhase.LOCALIZE, f"Recover from: {reason}", TaskContract.LOCALIZE.value, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)
            return [node]
        node = MissionNode(base_id, "Narrow repair", NodePhase.NARROW_FIX, f"Recover from: {reason}", TaskContract.NARROW_FIX.value, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)
        return [node]

    def expand_mission_graph(self, mission: Mission, extra_nodes: list[MissionNode]) -> Mission:
        existing_ids = {n.node_id for n in mission.nodes}
        for node in extra_nodes:
            if node.node_id not in existing_ids:
                mission.nodes.append(node)
        return replace(mission, nodes=list(mission.nodes))
