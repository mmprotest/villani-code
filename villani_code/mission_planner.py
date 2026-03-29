from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from villani_code.autonomy import TaskContract
from villani_code.mission import Mission, MissionNode, MissionType, NodePhase, NodeStatus


class MissionPlanner:
    def build_mission(self, objective: str, repo_root: str, repo_signals: dict[str, Any] | None = None) -> Mission:
        mission_type = self.classify_mission_type(objective, repo_signals=repo_signals)
        mission = Mission(
            mission_id=f"mission-{uuid.uuid4().hex[:10]}",
            user_goal=objective.strip() or "maintenance patrol and stabilization",
            mission_type=mission_type,
            success_criteria=self.derive_success_criteria(objective, mission_type),
            repo_root=repo_root,
        )
        mission.nodes = self.build_initial_nodes(mission, repo_signals=repo_signals)
        return mission

    def classify_mission_type(self, objective: str, repo_signals: dict[str, Any] | None = None) -> MissionType:
        low = (objective or "").lower()
        repo_signals = repo_signals or {}
        greenfield_prompt = any(
            phrase in low
            for phrase in [
                "build something",
                "make something",
                "create something",
                "start a project",
                "empty sandbox",
                "build a tool",
                "local tool",
                "something interesting",
                "something useful",
                "something cool",
            ]
        )
        if greenfield_prompt and (
            bool(repo_signals.get("workspace_empty_or_internal_only"))
            or bool(repo_signals.get("workspace_lightweight_hints_only"))
            or not bool(repo_signals.get("existing_project_detected"))
        ):
            return MissionType.GREENFIELD_BUILD
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
        if mission_type == MissionType.GREENFIELD_BUILD:
            return [
                "Choose one feasible project direction with rationale.",
                "Create runnable user-facing deliverables outside .villani/.",
                "Implement one real vertical slice with a usable entrypoint.",
                "Run targeted local validation and summarize run instructions.",
            ]
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
        maintenance_cmds = list(repo_signals.get("maintenance_commands", []) or [])
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

        if mission.mission_type == MissionType.GREENFIELD_BUILD:
            n1 = _n("inspect-workspace", "Inspect workspace", NodePhase.INSPECT_WORKSPACE, TaskContract.INSPECT.value, "Inspect workspace hints, constraints, and feasible build targets")
            n2 = _n("choose-direction", "Choose project direction", NodePhase.CHOOSE_PROJECT_DIRECTION, "choose_project_direction", "Generate candidates and choose one bounded, useful project direction", [n1.node_id])
            n3 = _n("scaffold-project", "Scaffold project", NodePhase.SCAFFOLD_PROJECT, "scaffold_project", "Scaffold chosen project in user workspace paths (not .villani/)", [n2.node_id])
            n4 = _n("vertical-slice", "Implement vertical slice", NodePhase.IMPLEMENT_VERTICAL_SLICE, "implement_vertical_slice", "Implement a minimal but real runnable slice", [n3.node_id])
            n5 = _n("validate-project", "Validate project", NodePhase.VALIDATE_PROJECT, "validate_project", "Run targeted validation / smoke checks for the built slice", [n4.node_id])
            n6 = _n("summarize-outcome", "Summarize outcome", NodePhase.SUMMARIZE_OUTCOME, "summarize_outcome", "Summarize what was built, why, and how to run it", [n5.node_id])
            nodes = [n1, n2, n3, n4, n5, n6]
        elif mission.mission_type in {MissionType.BUGFIX, MissionType.REPO_STABILIZATION, MissionType.NARROW_REFACTOR}:
            n1 = _n("localize", "Localize likely fault", NodePhase.LOCALIZE, TaskContract.LOCALIZE.value, mission.user_goal)
            n2 = _n("repro", "Reproduce failure", NodePhase.REPRODUCE, TaskContract.REPRODUCE.value, "Reproduce and capture failing signal", [n1.node_id])
            n3 = _n("fix", "Apply minimal fix", NodePhase.NARROW_FIX, TaskContract.NARROW_FIX.value, "Implement smallest safe change", [n2.node_id])
            n4 = _n("validate", "Validate fix", NodePhase.VALIDATE, TaskContract.VALIDATE.value, "Run targeted validation", [n3.node_id])
            n5 = _n("summary", "Summarize mission outcome", NodePhase.SUMMARIZE, TaskContract.SUMMARIZE.value, "Summarize evidence and remaining risks", [n4.node_id])
            nodes = [n1, n2, n3, n4, n5]
        elif mission.mission_type == MissionType.FEATURE:
            n1 = _n("inspect", "Inspect current implementation", NodePhase.INSPECT, TaskContract.INSPECT.value, mission.user_goal)
            n2 = _n("impl", "Implement feature", NodePhase.BROAD_FIX, TaskContract.IMPLEMENT.value, "Implement objective and related tests", [n1.node_id])
            n3 = _n("validate", "Validate feature", NodePhase.VALIDATE, TaskContract.VALIDATE.value, "Run feature-focused validation", [n2.node_id])
            n4 = _n("summary", "Summarize feature rollout", NodePhase.SUMMARIZE, TaskContract.SUMMARIZE.value, "Summarize feature and verification evidence", [n3.node_id])
            nodes = [n1, n2, n3, n4]
        elif mission.mission_type == MissionType.REGRESSION_CONTAINMENT:
            n1 = _n("localize", "Localize change impact", NodePhase.LOCALIZE, TaskContract.LOCALIZE.value, "Analyze diff and likely blast radius")
            n2 = _n("inspect", "Inspect blast radius", NodePhase.INSPECT, TaskContract.INSPECT.value, "Inspect impacted files and neighboring paths", [n1.node_id])
            n3 = _n("repair", "Contain regression fallout", NodePhase.NARROW_FIX, TaskContract.CONTAIN_CHANGE.value, "Implement smallest containment repair", [n2.node_id])
            n4 = _n("validate", "Run containment validation", NodePhase.VALIDATE, TaskContract.VALIDATE.value, "Run impacted tests and critical checks", [n3.node_id])
            n5 = _n("summary", "Summarize containment status", NodePhase.SUMMARIZE, TaskContract.SUMMARIZE.value, "Summarize stabilized vs. blocked fallout", [n4.node_id])
            nodes = [n1, n2, n3, n4, n5]
        elif mission.mission_type == MissionType.VALIDATION_ONLY:
            n1 = _n("validate", "Run validation", NodePhase.VALIDATE, TaskContract.VALIDATE.value, mission.user_goal)
            n2 = _n("summary", "Summarize validation", NodePhase.SUMMARIZE, TaskContract.SUMMARIZE.value, "Summarize validation evidence", [n1.node_id])
            nodes = [n1, n2]
        else:
            n1 = _n("inspect-repo", "Inspect repository health", NodePhase.INSPECT, TaskContract.INSPECT.value, "Map docs/test/config/tooling risk signals")
            n2 = _n("localize", "Localize maintenance target", NodePhase.LOCALIZE, TaskContract.LOCALIZE.value, "Localize highest-leverage maintenance target", [n1.node_id])
            n3 = _n("inspect-target", "Inspect localized area", NodePhase.INSPECT, TaskContract.INSPECT.value, "Inspect localized files for cleanup and reliability opportunities", [n2.node_id])
            n4 = _n("cleanup", "Apply narrow maintenance change", NodePhase.NARROW_FIX, TaskContract.CLEANUP.value, "Apply one focused maintenance improvement", [n3.node_id])
            n5 = _n("baseline", "Validate importability baseline", NodePhase.VALIDATE, TaskContract.VALIDATE.value, "Run importability and smoke validation", [n4.node_id])
            n5.validation_commands = list(dict.fromkeys((maintenance_cmds + validation)[:4]))
            n6 = _n("followup", "Light maintenance follow-up", NodePhase.NARROW_FIX, TaskContract.NARROW_FIX.value, "Optional follow-up touch-up if verification indicates", [n5.node_id])
            n7 = _n("validate-final", "Final maintenance validation", NodePhase.VALIDATE, TaskContract.VALIDATE.value, "Run final targeted validation", [n6.node_id])
            n8 = _n("summary", "Summarize maintenance pass", NodePhase.SUMMARIZE, TaskContract.SUMMARIZE.value, "Summarize completed maintenance and residual risks", [n7.node_id])
            nodes = [n1, n2, n3, n4, n5, n6, n7, n8]

        return nodes

    def spawn_recovery_nodes(self, mission: Mission, failed_node: MissionNode, strategy: str, reason: str) -> list[MissionNode]:
        base_id = f"{mission.mission_id}-recovery-{uuid.uuid4().hex[:6]}"
        validation = list(failed_node.validation_commands[:3])
        candidate_files = list(failed_node.candidate_files[:20])
        if mission.mission_type == MissionType.GREENFIELD_BUILD:
            if strategy == "broaden":
                return [MissionNode(base_id, "Broaden workspace inspection", NodePhase.INSPECT_WORKSPACE, f"Recover from: {reason}", TaskContract.INSPECT.value, candidate_files=candidate_files, validation_commands=validation, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)]
            if strategy == "simplify_direction":
                return [MissionNode(base_id, "Choose simpler project direction", NodePhase.CHOOSE_PROJECT_DIRECTION, f"Recover from: {reason}", "choose_project_direction", candidate_files=candidate_files, validation_commands=validation, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)]
            if strategy == "rescope":
                return [MissionNode(base_id, "Re-scope vertical slice", NodePhase.IMPLEMENT_VERTICAL_SLICE, f"Recover from: {reason}", "implement_vertical_slice", candidate_files=candidate_files, validation_commands=validation, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)]
            return [MissionNode(base_id, "Recover greenfield build", NodePhase.SCAFFOLD_PROJECT, f"Recover from: {reason}", "scaffold_project", candidate_files=candidate_files, validation_commands=validation, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)]

        if strategy == "broaden":
            return [MissionNode(base_id, "Broaden inspection", NodePhase.INSPECT, f"Recover from: {reason}", TaskContract.INSPECT.value, candidate_files=candidate_files, validation_commands=validation, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)]
        if strategy == "relocalize":
            return [MissionNode(base_id, "Re-localize root cause", NodePhase.LOCALIZE, f"Recover from: {reason}", TaskContract.LOCALIZE.value, candidate_files=candidate_files, validation_commands=validation, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)]
        return [MissionNode(base_id, "Narrow repair", NodePhase.NARROW_FIX, f"Recover from: {reason}", TaskContract.NARROW_FIX.value, candidate_files=candidate_files, validation_commands=validation, depends_on=[failed_node.node_id], created_from_node_id=failed_node.node_id, status=NodeStatus.READY)]

    def expand_mission_graph(self, mission: Mission, extra_nodes: list[MissionNode]) -> Mission:
        existing_ids = {n.node_id for n in mission.nodes}
        for node in extra_nodes:
            if node.node_id not in existing_ids:
                mission.nodes.append(node)
        return replace(mission, nodes=list(mission.nodes))
