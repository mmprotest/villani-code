from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from villani_code.autonomy import TaskContract, VerificationEngine
from villani_code.autonomous_reporting import build_mission_summary
from villani_code.autonomous_stop import evaluate_mission_stop
from villani_code.change_containment import build_change_containment_context, create_regression_containment_nodes
from villani_code.localization import LocalizationEngine, LocalizationResult
from villani_code.mission import (
    DeltaClassification,
    LocalizationSnapshot,
    Mission,
    MissionExecutionState,
    MissionScratchpad,
    MissionType,
    NodePhase,
    NodeStatus,
    NodeOutcomeRecord,
)
from villani_code.mission_bridge import execute_mission_node_with_runner
from villani_code.mission_planner import MissionPlanner
from villani_code.mission_store import append_mission_event, save_final_mission_report, save_mission_snapshot
from villani_code.path_authority import (
    INTERNAL_VILLANI_ROOTS,
    is_internal_villani_path,
    split_internal_paths,
)
from villani_code.recovery import RecoveryPlanner
from villani_code.repo_signal_planner import collect_repo_signals
from villani_code.runtime_safety import ensure_runtime_dependencies_not_shadowed
from villani_code.verification import (
    classify_node_outcome,
    compute_validation_delta,
    evaluate_mission_status,
    run_static_verification,
    run_validation_commands,
    summarize_validation_results,
    VerificationBaseline,
)


@dataclass(slots=True)
class VillaniModeConfig:
    enabled: bool = False
    steering_objective: str | None = None


@dataclass(slots=True)
class RepoSnapshot:
    repo_root: str
    tooling_commands: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AutonomousTask:
    task_id: str
    title: str
    rationale: str
    priority: float = 0.5
    confidence: float = 0.5
    verification_plan: list[str] = field(default_factory=list)
    task_contract: str = TaskContract.INSPECT.value
    status: str = "pending"
    outcome: str = ""
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    validation_artifacts: list[str] = field(default_factory=list)
    inspection_summary: str = ""
    runner_failures: list[str] = field(default_factory=list)
    intentional_changes: list[str] = field(default_factory=list)
    incidental_changes: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    produced_effect: bool = False
    produced_validation: bool = False
    produced_inspection_conclusion: bool = False
    terminated_reason: str = ""
    attempts: int = 0


class VillaniModeController:
    """Mission-driven autonomous Villani mode that uses the existing runner as execution engine."""

    def __init__(
        self,
        runner: Any,
        repo: Path,
        steering_objective: str | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        takeover_config: Any | None = None,
    ) -> None:
        self.runner = runner
        self.repo = repo.resolve()
        ensure_runtime_dependencies_not_shadowed(self.repo)
        self.steering_objective = steering_objective
        self.event_callback = event_callback or (lambda _event: None)
        self.planner = MissionPlanner()
        self.localization = LocalizationEngine(self.repo)
        self.recovery = RecoveryPlanner(self.planner)
        self._repo_signals: dict[str, Any] = {}
        self.takeover_config = takeover_config
        self.verifier = VerificationEngine(self.repo)

    def _extract_user_space_deliverables(self, changed_files: list[str], execution_payload: dict[str, Any]) -> list[str]:
        candidates = list(changed_files)
        for key in ("intentional_changes", "incidental_changes", "changed_files"):
            candidates.extend(str(x) for x in list(execution_payload.get(key, []) or []))
        user_deliverables, _internal = split_internal_paths([str(item or "").strip() for item in candidates])
        return user_deliverables

    def _record_greenfield_progress(
        self,
        execution_state: MissionExecutionState,
        node: Any,
        changed_files: list[str],
        execution_payload: dict[str, Any],
        node_status: str,
    ) -> list[str]:
        if execution_state.mission.mission_type != MissionType.GREENFIELD_BUILD:
            return []
        progress = dict(execution_state.greenfield_progress or {})
        deliverables = self._extract_user_space_deliverables(changed_files, execution_payload)
        current_paths = [str(x) for x in list(progress.get("deliverable_paths", []) or []) if str(x).strip()]
        merged_paths = sorted(dict.fromkeys(current_paths + deliverables))
        progress["deliverable_paths"] = merged_paths
        progress["created_deliverables"] = merged_paths
        scaffold_success = bool(progress.get("successful_greenfield_scaffold"))
        if node.phase == NodePhase.SCAFFOLD_PROJECT and node_status == "passed" and deliverables:
            scaffold_success = True
        progress["successful_greenfield_scaffold"] = scaffold_success
        source_nodes = [str(x) for x in list(progress.get("source_nodes", []) or []) if str(x).strip()]
        if deliverables and node.node_id not in source_nodes:
            source_nodes.append(node.node_id)
        progress["source_nodes"] = source_nodes[-20:]
        execution_state.greenfield_progress = progress
        execution_state.mission.mission_context["greenfield_progress"] = dict(progress)
        return deliverables

    def _initialize_scratchpad(self, mission: Mission, repo_signals: dict[str, Any]) -> MissionScratchpad:
        scratchpad = MissionScratchpad(
            mission_goal=mission.user_goal,
            mission_type=mission.mission_type.value,
            current_phase=mission.nodes[0].phase.value if mission.nodes else "",
            hard_constraints=list(mission.success_criteria),
            allowed_output_locations=["workspace_user_paths_only"],
            ignored_internal_paths=list(repo_signals.get("ignored_context_paths", [f"{root}/" for root in INTERNAL_VILLANI_ROOTS])),
            validation_intent="run_targeted_validation_after_changes",
            no_confirmation_required=True,
            no_internal_artifact_deliverables=mission.mission_type == MissionType.GREENFIELD_BUILD,
            workspace_classification=(
                "empty"
                if repo_signals.get("workspace_empty_or_internal_only")
                else ("lightly_suggestive" if repo_signals.get("workspace_lightweight_hints_only") else "existing_project")
            ),
            minimal_vertical_slice_target="runnable_entrypoint_with_smoke_validation" if mission.mission_type == MissionType.GREENFIELD_BUILD else "",
            path_authority=dict(repo_signals.get("path_authority", {})),
        )
        scratchpad.next_required_action = scratchpad.derive_next_action()
        return scratchpad

    def _refresh_scratchpad_pre_node(self, execution_state: MissionExecutionState, node: Any) -> None:
        scratchpad = execution_state.scratchpad
        scratchpad.current_phase = node.phase.value
        if execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD:
            scratchpad.mission_type = MissionType.GREENFIELD_BUILD.value
            if execution_state.greenfield_selection:
                scratchpad.update_from_greenfield_selection(execution_state.greenfield_selection, execution_state.greenfield_candidates)
            if scratchpad.chosen_project_direction and node.phase == NodePhase.CHOOSE_PROJECT_DIRECTION:
                node.objective = f"Refine and commit chosen direction ({scratchpad.chosen_project_direction}) with bounded scope."
            if scratchpad.confirmed_deliverables and node.phase == NodePhase.INSPECT_WORKSPACE:
                node.status = NodeStatus.SKIPPED
                return
        scratchpad.next_required_action = scratchpad.derive_next_action()

    def _apply_no_regression_guards(self, execution_state: MissionExecutionState, outcome: dict[str, Any]) -> None:
        scratchpad = execution_state.scratchpad
        mission = execution_state.mission
        if scratchpad.mission_type == MissionType.GREENFIELD_BUILD.value and mission.mission_type != MissionType.GREENFIELD_BUILD:
            mission.mission_type = MissionType.GREENFIELD_BUILD
        if scratchpad.chosen_project_direction and not execution_state.greenfield_selection.get("project_type"):
            execution_state.greenfield_selection["project_type"] = scratchpad.chosen_project_direction
        if scratchpad.confirmed_deliverables and not outcome.get("user_deliverable_patch") and outcome.get("status") == "failed":
            outcome["status"] = "partial"
            outcome["reason"] = "no-regression guard: prior deliverables confirmed in scratchpad"

    def run(self) -> dict[str, Any]:
        if not (self.steering_objective or "").strip():
            snapshot = self.inspect_repo()
            ranked = self.rank_tasks(self.generate_candidates(snapshot))
            if not ranked or float(ranked[0].confidence) < 0.35:
                return {
                    "done_reason": "No opportunities above confidence threshold.",
                    "tasks_attempted": [],
                    "blockers": [],
                    "files_changed": [],
                    "recommended_next_steps": ["Refine goal and rerun Villani mode with a tighter objective."],
                    "working_memory": {"model_request_count": 0, "planner_only_cycles": 1, "followup_skip_reasons": ["below_threshold"], "stop_decision_kind": "below_threshold"},
                }
        mission_state = self._initialize_mission()
        save_mission_snapshot(str(self.repo), mission_state.mission, mission_state.to_dict())

        while True:
            done = self._mission_done(mission_state)
            if done is not None:
                break
            node = self._select_next_node(mission_state)
            if node is None:
                self._activity("Waiting for ready node; incrementing no-progress counter.")
                mission_state.consecutive_no_progress += 1
                continue
            result = self._execute_node(mission_state, node)
            outcome = self._evaluate_node(mission_state, node, result)
            if outcome.get("status") in {"failed", "stale", "partial"}:
                self._handle_recovery(mission_state, node, outcome)

            save_mission_snapshot(str(self.repo), mission_state.mission, mission_state.to_dict())

        return self._finalize_mission(mission_state)

    def _initialize_mission(self) -> MissionExecutionState:
        self._activity("Initializing mission and collecting repository signals.")
        objective = (self.steering_objective or "").strip()
        self._repo_signals = self._collect_repo_signals()
        mission = self.planner.build_mission(objective, str(self.repo), repo_signals=self._repo_signals)
        if mission.mission_type == MissionType.GREENFIELD_BUILD:
            candidates, selection = self._plan_greenfield_direction(objective, self._repo_signals)
            mission.mission_context["greenfield_candidates"] = candidates
            mission.mission_context["greenfield_selection"] = selection

        if mission.mission_type == MissionType.REGRESSION_CONTAINMENT:
            context = build_change_containment_context(str(self.repo))
            containment_nodes = create_regression_containment_nodes(mission, context)
            mission.nodes = containment_nodes

        if not objective:
            mission.user_goal = "Perform maintenance patrol and stabilize highest-leverage issue."
            mission.mission_type = MissionType.MAINTENANCE

        baseline = self._git_changed_files()
        state = MissionExecutionState(
            mission=mission,
            changed_files_baseline=baseline,
            scratchpad=self._initialize_scratchpad(mission, self._repo_signals),
        )
        if mission.mission_type == MissionType.GREENFIELD_BUILD:
            state.greenfield_candidates = list(mission.mission_context.get("greenfield_candidates", []))
            state.greenfield_selection = dict(mission.mission_context.get("greenfield_selection", {}))
            state.greenfield_progress = dict(mission.mission_context.get("greenfield_progress", {}) or {})
            state.scratchpad.update_from_greenfield_selection(state.greenfield_selection, state.greenfield_candidates)
        append_mission_event(str(self.repo), mission.mission_id, {"type": "mission_initialized", "goal": mission.user_goal, "mission_type": mission.mission_type.value})
        return state

    def _collect_repo_signals(self) -> dict[str, Any]:
        return collect_repo_signals(str(self.repo))

    def _plan_greenfield_direction(self, objective: str, signals: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        prompt = (objective or "").lower()
        hint_files = list(signals.get("workspace_hint_files", []) or [])
        sample_data = list(signals.get("sample_data_files", []) or [])
        language_hints = list(signals.get("language_hints", []) or [])
        likely = list(signals.get("likely_project_directions", []) or [])
        candidates: list[dict[str, Any]] = [
            {"project_type": "python_cli_utility", "utility": 0.88, "feasibility": 0.9, "fit": 0.6, "validation": 0.9, "bounded_scope": 0.9, "rationale": "Fast local tool with clear smoke validation."},
            {"project_type": "file_report_generator", "utility": 0.82, "feasibility": 0.88, "fit": 0.58, "validation": 0.88, "bounded_scope": 0.92, "rationale": "Produces concrete output artifact and easy run path."},
        ]
        if sample_data:
            candidates.append({"project_type": "data_quality_checker", "utility": 0.92, "feasibility": 0.86, "fit": 0.94, "validation": 0.9, "bounded_scope": 0.82, "rationale": "Workspace includes sample data; quality checks are directly useful."})
        if "python" in language_hints:
            candidates.append({"project_type": "csv_analysis_cli", "utility": 0.87, "feasibility": 0.86, "fit": 0.78, "validation": 0.86, "bounded_scope": 0.84, "rationale": "Python tooling hints support a compact analysis CLI."})
        for c in candidates:
            if c["project_type"] in likely:
                c["fit"] = round(min(1.0, float(c["fit"]) + 0.12), 2)
            if "useful" in prompt:
                c["utility"] = round(min(1.0, float(c["utility"]) + 0.05), 2)
            if "interesting" in prompt or "cool" in prompt:
                c["fit"] = round(min(1.0, float(c["fit"]) + 0.03), 2)
            c["score"] = round((0.3 * c["utility"]) + (0.25 * c["feasibility"]) + (0.2 * c["fit"]) + (0.15 * c["validation"]) + (0.1 * c["bounded_scope"]), 3)
        deterministic_preference = {
            "python_cli_utility": 0,
            "file_report_generator": 1,
            "data_quality_checker": 2,
            "csv_analysis_cli": 3,
        }
        ranked = sorted(
            candidates,
            key=lambda item: (
                -float(item.get("score", 0.0)),
                deterministic_preference.get(str(item.get("project_type", "")), 99),
                str(item.get("project_type", "")),
            ),
        )
        chosen = dict(ranked[0]) if ranked else {"project_type": "python_cli_utility", "rationale": "default fallback"}
        selection = {
            "project_type": chosen.get("project_type", "python_cli_utility"),
            "selection_rationale": chosen.get("rationale", ""),
            "constraints": {
                "avoid_internal_deliverables": True,
                "docs_only_invalid_for_open_greenfield": True,
                "target_paths": "workspace_user_paths_only",
                "single_mission_scope": True,
            },
            "hint_files_considered": hint_files[:10],
        }
        return ranked[:4], selection

    def _select_next_node(self, execution_state: MissionExecutionState):
        self._activity("Selecting next ready mission node.")
        mission = execution_state.mission
        self._hydrate_nodes_from_localization(execution_state)
        for node in mission.nodes:
            if node.status == NodeStatus.PENDING and all(self._node_by_id(mission, dep).status == NodeStatus.SUCCEEDED for dep in node.depends_on if self._node_by_id(mission, dep)):
                node.status = NodeStatus.READY
        ready_nodes = [n for n in mission.nodes if n.status == NodeStatus.READY]
        if not ready_nodes:
            return None
        selected = sorted(ready_nodes, key=lambda n: (n.priority, n.confidence), reverse=True)[0]
        self._refresh_scratchpad_pre_node(execution_state, selected)
        if selected.status == NodeStatus.SKIPPED:
            return None
        return selected

    def _execute_node(self, execution_state: MissionExecutionState, node: Any) -> dict[str, Any]:
        self._activity(f"Executing node {node.node_id} ({node.phase.value}).")
        node.status = NodeStatus.RUNNING
        node.attempts += 1
        execution_state.active_node_id = node.node_id

        localization_result = None
        if node.phase == NodePhase.LOCALIZE:
            localization_result = self._run_localization_node(execution_state, node)

        mission_result = execute_mission_node_with_runner(self.runner, execution_state.mission, node, execution_state)

        if node.phase == NodePhase.LOCALIZE and mission_result.failures:
            loc_from_output = self.localization.localize_from_failure_output(
                "\n".join(x.get("command", "") for x in mission_result.commands),
                "\n".join(mission_result.failures),
                self._repo_signals,
                structured_signals={
                    "changed_files": mission_result.changed_files,
                    "validation_commands": mission_result.commands_run,
                    "failed_commands": [x.get("command", "") for x in mission_result.commands if int(x.get("exit", 0)) != 0],
                },
            )
            localization_result = self._merge_localization_results(localization_result, loc_from_output)
            self._apply_localization(execution_state, node, localization_result)

        append_mission_event(
            str(self.repo),
            execution_state.mission.mission_id,
            {
                "type": "node_executed",
                "node_id": node.node_id,
                "changed_files": mission_result.changed_files,
                "localization_files": list(localization_result.target_files[:8]) if localization_result else [],
            },
        )
        return {
            "runner_result": mission_result,
            "changed_files": mission_result.changed_files,
            "commands": mission_result.commands,
            "commands_run": mission_result.commands_run,
            "command_results": mission_result.commands,
            "failures": mission_result.failures,
            "tool_failures": mission_result.tool_failures,
            "patch_detected": mission_result.patch_detected,
            "meaningful_patch": mission_result.meaningful_patch,
            "transcript_summary": mission_result.transcript_summary,
            "model_activity": mission_result.model_activity,
            "acted": mission_result.acted,
            "prose_only": mission_result.prose_only,
            "clarification_requested": mission_result.clarification_requested,
            "execution_payload": mission_result.execution_payload,
            "localization": localization_result,
        }

    def _run_localization_node(self, execution_state: MissionExecutionState, node: Any) -> LocalizationResult:
        self._activity("Running first-class localization before runner execution.")
        seed = "\n".join([execution_state.mission.user_goal, node.objective, " ".join(execution_state.evidence_log[-10:])])
        loc = self.localization.localize_from_goal(
            seed,
            self._repo_signals,
            structured_signals={
                "changed_files": execution_state.latest_changed_files,
                "validation_commands": execution_state.latest_validation_summary.get("commands", []),
                "failed_commands": [
                    str(item.get("command", ""))
                    for item in execution_state.latest_command_results
                    if int(item.get("exit", 1)) != 0
                ],
            },
        )
        self._apply_localization(execution_state, node, loc)
        return loc

    def _apply_localization(self, execution_state: MissionExecutionState, node: Any, result: LocalizationResult | None) -> None:
        if not result:
            return
        snapshot = LocalizationSnapshot(
            target_files=list(result.target_files),
            likely_bug_class=result.likely_bug_class,
            repair_intent=result.repair_intent,
            confidence=float(result.confidence),
            evidence=list(result.evidence),
            suggested_validation_commands=list(result.suggested_validation_commands),
        )
        if execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD:
            snapshot.target_files, _internal_targets = split_internal_paths(snapshot.target_files)
            snapshot.evidence = [e for e in snapshot.evidence if not any(f"{root}/" in e for root in INTERNAL_VILLANI_ROOTS)]
        node.localization = snapshot
        node.candidate_files = list(dict.fromkeys(snapshot.target_files + node.candidate_files))[:20]
        node.validation_commands = list(dict.fromkeys(snapshot.suggested_validation_commands + node.validation_commands))[:6]
        node.confidence = max(node.confidence, snapshot.confidence)
        execution_state.last_localization = snapshot
        execution_state.localization_history.append(snapshot)
        execution_state.evidence_log.extend([f"localize:{e}" for e in snapshot.evidence])
        for ranked in result.ranked_candidates[:3]:
            execution_state.evidence_log.append(f"localize_rank:{ranked.file_path}:{ranked.score}")

    def _merge_localization_results(self, base: LocalizationResult | None, extra: LocalizationResult) -> LocalizationResult:
        if base is None:
            return extra
        merged = LocalizationResult(
            target_files=list(dict.fromkeys(base.target_files + extra.target_files)),
            likely_bug_class=extra.likely_bug_class if extra.confidence >= base.confidence else base.likely_bug_class,
            repair_intent=extra.repair_intent or base.repair_intent,
            confidence=max(base.confidence, extra.confidence),
            evidence=list(dict.fromkeys(base.evidence + extra.evidence)),
            suggested_validation_commands=list(dict.fromkeys(base.suggested_validation_commands + extra.suggested_validation_commands)),
        )
        return merged

    def _hydrate_nodes_from_localization(self, execution_state: MissionExecutionState) -> None:
        loc = execution_state.last_localization
        if not loc.target_files:
            return
        for node in execution_state.mission.nodes:
            if node.status not in {NodeStatus.PENDING, NodeStatus.READY}:
                continue
            if node.phase in {NodePhase.INSPECT, NodePhase.REPRODUCE, NodePhase.NARROW_FIX, NodePhase.BROAD_FIX, NodePhase.VALIDATE, NodePhase.RECOVER}:
                node.candidate_files = list(dict.fromkeys(loc.target_files + node.candidate_files))[:20]
                node.validation_commands = list(dict.fromkeys(loc.suggested_validation_commands + node.validation_commands))[:6]
                node.confidence = max(node.confidence, min(0.95, loc.confidence + 0.05))
                if loc.repair_intent and loc.repair_intent not in node.evidence:
                    node.evidence.append(f"localized_intent:{loc.repair_intent}")
            if node.phase in {NodePhase.SCAFFOLD_PROJECT, NodePhase.IMPLEMENT_VERTICAL_SLICE} and execution_state.greenfield_selection:
                chosen = str(execution_state.greenfield_selection.get("project_type", "")).strip()
                if chosen:
                    node.evidence.append(f"greenfield_selected:{chosen}")

    def _evaluate_node(self, execution_state: MissionExecutionState, node: Any, node_result: dict[str, Any]) -> dict[str, Any]:
        self._activity(f"Evaluating node outcome for {node.node_id} with contract-aware verification.")
        changed_files = list(node_result.get("changed_files", []))
        internal_changed_files = list(node_result.get("internal_changed_files", []))
        baseline = VerificationBaseline(
            changed_files=list(execution_state.latest_changed_files),
            validation_summary=dict(execution_state.latest_validation_summary),
            failure_fingerprints=list(execution_state.failure_fingerprint_history[-6:]),
            localization={
                "target_files": list(execution_state.last_localization.target_files),
                "confidence": execution_state.last_localization.confidence,
                "likely_bug_class": execution_state.last_localization.likely_bug_class,
                "repair_intent": execution_state.last_localization.repair_intent,
            },
            execution_snapshot=dict(execution_state.latest_execution_payload),
            previous_command_results=list(execution_state.latest_command_results),
        )
        static_result = run_static_verification(str(self.repo), changed_files)
        commands = node.validation_commands or self._repo_signals.get("likely_validation_commands", ["pytest -q"])
        runner_command_results = list(node_result.get("command_results", []) or [])
        command_results: list[dict[str, Any]] = []
        execution_payload = dict(node_result.get("execution_payload", {}) or {})
        payload_command_results = list(execution_payload.get("command_results", []) or [])
        if runner_command_results:
            command_results = list(runner_command_results)
        elif payload_command_results:
            command_results = list(payload_command_results)
        elif commands:
            command_results = run_validation_commands(str(self.repo), commands[:3])
        normalized_command_results: dict[str, dict[str, Any]] = {}
        for record in command_results:
            cmd_key = str(record.get("command", "")).strip() or f"__index_{len(normalized_command_results)}"
            normalized_command_results[cmd_key] = dict(record)
        command_results = list(normalized_command_results.values())
        validation_summary = summarize_validation_results(command_results)
        validation_delta = compute_validation_delta(
            baseline.validation_summary,
            baseline.previous_command_results,
            command_results,
        ).to_dict()
        localization_payload = self._localization_payload(node, node_result, execution_state)
        previous_loc = execution_state.localization_history[-2] if len(execution_state.localization_history) > 1 else LocalizationSnapshot()

        outcome = classify_node_outcome(
            node.contract_type,
            static_result,
            command_results,
            changed_files,
            prose_only=bool(node_result.get("prose_only")),
            localization=localization_payload,
            prior_fingerprints=execution_state.failure_fingerprint_history,
            previous_localization={
                "target_files": previous_loc.target_files,
                "confidence": previous_loc.confidence,
            },
            baseline=baseline,
            validation_summary=validation_summary,
            execution_payload=execution_payload,
            validation_delta=validation_delta,
            mission_type=execution_state.mission.mission_type.value,
            node_phase=node.phase.value,
            clarification_requested=bool(node_result.get("clarification_requested")),
            scratchpad=execution_state.scratchpad,
        )
        user_deliverables = self._extract_user_space_deliverables(changed_files, execution_payload)
        if (
            execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD
            and node.phase == NodePhase.SCAFFOLD_PROJECT
            and user_deliverables
        ):
            outcome["status"] = "passed"
            outcome["reason"] = "greenfield scaffold created user-space deliverables"
            outcome["patch_no_improvement"] = False
            outcome["validation_worsened"] = False

        failure_fingerprint = validation_summary.get("failure_fingerprints", [""])[0] if validation_summary.get("failure_fingerprints") else ""
        if failure_fingerprint:
            node.failure_fingerprint = failure_fingerprint
            execution_state.failure_fingerprint_history.append(failure_fingerprint)

        node.last_outcome = NodeOutcomeRecord(
            status=str(outcome.get("status", "unknown")),
            delta_classification=str(outcome.get("delta_classification", DeltaClassification.AMBIGUOUS.value)),
            delta_reason=str(outcome.get("delta_reason", "")),
            changed_files=list(changed_files),
            patch_detected=bool(outcome.get("patch_exists")),
            meaningful_patch=bool(outcome.get("meaningful_patch")),
            validation_summary=validation_summary,
            failure_fingerprint=failure_fingerprint,
            localization_evidence=list(localization_payload.get("evidence", [])),
        )
        execution_state.latest_validation_summary = dict(validation_summary)
        execution_state.latest_changed_files = list(changed_files)
        execution_state.latest_execution_payload = dict(execution_payload)
        execution_state.latest_command_results = list(command_results)
        persisted_deliverables = self._record_greenfield_progress(
            execution_state,
            node,
            changed_files,
            execution_payload,
            str(outcome.get("status", "")),
        )
        execution_state.scratchpad.update_from_execution_result(
            node.phase.value,
            str(outcome.get("status", "")),
            list(changed_files),
            list(node.blockers),
        )
        execution_state.scratchpad.update_from_verification(
            persisted_deliverables if execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD else outcome.get("user_space_changed_files", []),
            validation_summary,
            next_action=execution_state.scratchpad.derive_next_action(),
        )
        if execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD:
            execution_state.scratchpad.has_user_space_scaffolding = execution_state.scratchpad.has_user_space_scaffolding or bool(persisted_deliverables)
            execution_state.scratchpad.has_runnable_entrypoint = execution_state.scratchpad.has_runnable_entrypoint or (
                node.phase in {NodePhase.IMPLEMENT_VERTICAL_SLICE, NodePhase.VALIDATE_PROJECT}
                and str(outcome.get("status", "")) == "passed"
            )
        self._apply_no_regression_guards(execution_state, outcome)
        execution_state.mission.mission_context["scratchpad"] = execution_state.scratchpad.to_dict()

        node.evidence.extend(static_result.get("findings", []))
        node.evidence.extend([f"cmd:{r.get('command')} exit={r.get('exit')}" for r in command_results])
        execution_state.verification_history.append(
            {
                "node_id": node.node_id,
                "static": static_result,
                "commands": command_results,
                "validation_summary": validation_summary,
                "outcome": outcome,
                "changed_files": changed_files,
                "internal_changed_files": internal_changed_files,
                "failure_fingerprint": failure_fingerprint,
                "localization": localization_payload,
                "execution_payload": execution_payload,
                "validation_delta": validation_delta,
                "greenfield_deliverables": persisted_deliverables,
            }
        )

        if outcome["status"] == "passed":
            node.status = NodeStatus.SUCCEEDED
            execution_state.consecutive_no_progress = 0
            execution_state.repeated_delta_states = 0
        elif outcome["status"] == "stale":
            node.status = NodeStatus.FAILED
            execution_state.consecutive_no_model_activity += 1
            execution_state.consecutive_no_progress += 1
        else:
            node.status = NodeStatus.FAILED
            execution_state.consecutive_no_progress += 1
            if outcome.get("delta_classification") in {
                DeltaClassification.NO_IMPROVEMENT.value,
                DeltaClassification.AMBIGUOUS.value,
            }:
                execution_state.repeated_delta_states += 1
            else:
                execution_state.repeated_delta_states = 0

        append_mission_event(str(self.repo), execution_state.mission.mission_id, {"type": "node_evaluated", "node_id": node.node_id, "status": node.status.value, "outcome": outcome, "changed_files": changed_files, "validation": validation_summary})
        return {
            **outcome,
            "changed_files": changed_files,
            "internal_changed_files": internal_changed_files,
            "patch_detected": bool(outcome.get("patch_exists")),
            "meaningful_patch": bool(outcome.get("meaningful_patch")),
            "validation_summary": validation_summary,
            "failure_fingerprint": failure_fingerprint,
            "localization_evidence": list(localization_payload.get("evidence", [])),
        }

    def _localization_payload(self, node: Any, node_result: dict[str, Any], execution_state: MissionExecutionState) -> dict[str, Any]:
        loc_result = node_result.get("localization")
        if isinstance(loc_result, LocalizationResult):
            return {
                "target_files": loc_result.target_files,
                "likely_bug_class": loc_result.likely_bug_class,
                "repair_intent": loc_result.repair_intent,
                "confidence": loc_result.confidence,
                "evidence": loc_result.evidence,
                "suggested_validation_commands": loc_result.suggested_validation_commands,
            }
        loc = node.localization if node.localization.target_files else execution_state.last_localization
        return {
            "target_files": list(loc.target_files),
            "likely_bug_class": loc.likely_bug_class,
            "repair_intent": loc.repair_intent,
            "confidence": loc.confidence,
            "evidence": list(loc.evidence),
            "suggested_validation_commands": list(loc.suggested_validation_commands),
        }

    def _handle_recovery(self, execution_state: MissionExecutionState, node: Any, outcome: dict[str, Any]) -> None:
        self._activity(f"Planning recovery branch for node {node.node_id}.")
        outcome["authoritative_direction"] = execution_state.scratchpad.chosen_project_direction
        outcome["ignored_internal_paths"] = list(execution_state.scratchpad.ignored_internal_paths)
        if execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD:
            progress = dict(execution_state.greenfield_progress or {})
            has_scaffold_success = bool(progress.get("successful_greenfield_scaffold"))
            if has_scaffold_success and node.phase in {
                NodePhase.INSPECT_WORKSPACE,
                NodePhase.CHOOSE_PROJECT_DIRECTION,
                NodePhase.SCAFFOLD_PROJECT,
            }:
                append_mission_event(
                    str(self.repo),
                    execution_state.mission.mission_id,
                    {
                        "type": "recovery_suppressed",
                        "node_id": node.node_id,
                        "reason": "authoritative scaffold success already captured",
                    },
                )
                return
        outcome["localization_weak"] = bool(outcome.get("localization_weak")) or (node.phase.value == "localize" and node.confidence < 0.55)
        decision = self.recovery.plan_recovery(execution_state, node, outcome)
        if decision.mark_blocked:
            node.status = NodeStatus.BLOCKED
            node.blockers.append(decision.reason)
        elif decision.mark_exhausted:
            node.status = NodeStatus.EXHAUSTED
        else:
            self.planner.expand_mission_graph(execution_state.mission, decision.nodes)
        append_mission_event(str(self.repo), execution_state.mission.mission_id, {"type": "recovery", "node_id": node.node_id, "strategy": decision.strategy, "reason": decision.reason, "spawned_nodes": [n.node_id for n in decision.nodes]})

    def _mission_done(self, execution_state: MissionExecutionState):
        outcome, reason = evaluate_mission_status(execution_state)
        if outcome is not None:
            execution_state.mission.final_outcome = outcome.value
            execution_state.mission.stop_reason = reason
            execution_state.mission.state = "finished"
            return outcome
        outcome2, reason2 = evaluate_mission_stop(execution_state)
        if outcome2 is not None:
            execution_state.mission.final_outcome = outcome2.value
            execution_state.mission.stop_reason = reason2
            execution_state.mission.state = "finished"
            return outcome2
        return None

    def _finalize_mission(self, execution_state: MissionExecutionState) -> dict[str, Any]:
        self._activity("Finalizing mission report and summarizing outcomes.")
        mission = execution_state.mission
        touched = sorted(set(self._git_changed_files()) - set(execution_state.changed_files_baseline))
        touched, internal_touched = split_internal_paths(touched)
        if mission.mission_type == MissionType.GREENFIELD_BUILD:
            persisted = list(dict(execution_state.greenfield_progress or {}).get("deliverable_paths", []) or [])
            touched = sorted(dict.fromkeys(touched + [str(p) for p in persisted if str(p).strip()]))
        report = build_mission_summary(
            mission,
            execution_state,
            files_touched=touched,
            outcome=mission.final_outcome or "exhausted",
            stop_reason=mission.stop_reason or "Mission ended without explicit stop reason.",
        )
        if internal_touched:
            report.setdefault("greenfield_report", {})
            if isinstance(report["greenfield_report"], dict):
                report["greenfield_report"]["internal_artifacts"] = sorted(
                    dict.fromkeys(list(report["greenfield_report"].get("internal_artifacts", [])) + internal_touched)
                )
        save_final_mission_report(str(self.repo), mission, report)
        append_mission_event(str(self.repo), mission.mission_id, {"type": "mission_finalized", "outcome": mission.final_outcome, "stop_reason": mission.stop_reason})
        text = self.format_summary(report)
        return {
            "mission": mission.to_dict(),
            "report": report,
            "response": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }

    def _node_by_id(self, mission: Mission, node_id: str):
        for n in mission.nodes:
            if n.node_id == node_id:
                return n
        return None

    def _git_changed_files(self) -> list[str]:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True)
        if proc.returncode != 0:
            return []
        paths: list[str] = []
        for raw in proc.stdout.splitlines():
            line = raw.rstrip()
            if not line:
                continue
            path = line[3:] if len(line) > 3 else ""
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            path = path.strip()
            if path:
                paths.append(path)
        return sorted(dict.fromkeys(paths))

    def _activity(self, message: str) -> None:
        event = {"type": "villani_activity", "message": message}
        self.event_callback(event)
        print(f"[villani] {message}")

    # --- Legacy compatibility helpers used by existing tests/tooling ---
    def inspect_repo(self) -> RepoSnapshot:
        signals = self._collect_repo_signals()
        files: list[str] = []
        for path in self.repo.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                rel = path.relative_to(self.repo).as_posix()
                if is_internal_villani_path(rel):
                    continue
                files.append(rel)
        return RepoSnapshot(repo_root=str(self.repo), tooling_commands=list(signals.get("likely_validation_commands", [])), files=files[:200])

    def generate_candidates(self, snapshot: RepoSnapshot) -> list[AutonomousTask]:
        candidates = [
            AutonomousTask("inspect-1", "Inspect repo for highest-leverage improvement", "baseline inspection", priority=0.8, confidence=0.8, verification_plan=snapshot.tooling_commands[:2], task_contract=TaskContract.INSPECT.value),
            AutonomousTask("validate-1", "Validate baseline importability", "baseline validation", priority=0.7, confidence=0.75, verification_plan=["python -c 'import villani_code'"], task_contract=TaskContract.VALIDATION.value),
        ]
        if any(p.startswith("tests/") for p in snapshot.files):
            candidates.append(AutonomousTask("tests-1", "Run baseline tests", "tests detected", priority=0.75, confidence=0.72, verification_plan=["pytest -q"], task_contract=TaskContract.VALIDATE.value))
        return candidates

    @staticmethod
    def rank_tasks(tasks: list[AutonomousTask]) -> list[AutonomousTask]:
        return sorted(tasks, key=lambda t: (t.priority, t.confidence), reverse=True)

    def _extract_commands(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tr in (result.get("transcript", {}) or {}).get("tool_results", []):
            content = str(tr.get("content", "")).strip()
            if content.startswith("{") and "command" in content:
                try:
                    import json

                    payload = json.loads(content)
                    out.append({"command": str(payload.get("command", "")).strip(), "exit": int(payload.get("exit_code", payload.get("exit", 1)))})
                except Exception:
                    continue
        return out

    def _has_real_validation_artifact(self, task: AutonomousTask) -> bool:
        for artifact in task.validation_artifacts:
            low = str(artifact).lower()
            if "exit=0" in low or "(exit=0)" in low:
                return True
        return False

    def _adjudicate_task(self, task: AutonomousTask, verification: Any) -> tuple[str, str]:
        contract = str(task.task_contract)
        if task.runner_failures:
            return "failed", "runner_failures_present"
        if contract in {TaskContract.VALIDATION.value, TaskContract.VALIDATE.value}:
            if not task.produced_validation and not self._has_real_validation_artifact(task):
                return "failed", "validation_not_executed"
            return "passed", "validation_satisfied"
        if contract in {TaskContract.INSPECTION.value, TaskContract.INSPECT.value}:
            if task.produced_inspection_conclusion and task.inspection_summary.strip():
                return "passed", "inspection_completed"
            return "failed", "inspection_incomplete"
        if contract in {TaskContract.EFFECTFUL.value, TaskContract.NARROW_FIX.value, TaskContract.BROAD_FIX.value, TaskContract.IMPLEMENT.value, TaskContract.CLEANUP.value}:
            if task.produced_effect and bool(task.intentional_changes):
                return "passed", "effectful_change_detected"
            return "failed", "no_effectful_change"
        return "failed", "contract_not_satisfied"

    def _execute_task(self, task: AutonomousTask) -> AutonomousTask:
        self.event_callback({"type": "villani_model_request_started", "task_id": task.task_id, "title": task.title})
        prompt = f"Task: {task.title}\nReason: {task.rationale}\nNo network. Keep scope narrow.\nValidation plan: {'; '.join(task.verification_plan[:3])}"
        result = self.runner.run(prompt, execution_budget=None)
        self.event_callback({"type": "villani_model_request_finished", "task_id": task.task_id, "title": task.title})

        execution = (result or {}).get("execution", {}) if isinstance(result, dict) else {}
        task.terminated_reason = str(execution.get("terminated_reason", ""))
        task.validation_artifacts = list(execution.get("validation_artifacts", []) or [])
        task.runner_failures = list(execution.get("tool_failures", execution.get("runner_failures", [])) or [])
        task.inspection_summary = str(execution.get("inspection_summary", "") or "")
        task.intentional_changes = list(execution.get("intentional_changes", []) or [])
        task.incidental_changes = list(execution.get("incidental_changes", []) or [])
        task.files_changed = list(execution.get("changed_files", execution.get("files_changed", [])) or task.intentional_changes)
        task.produced_effect = bool(task.intentional_changes)
        task.produced_validation = self._has_real_validation_artifact(task)
        task.produced_inspection_conclusion = bool(task.inspection_summary.strip())
        verification = self.verifier.verify(task.title, task.files_changed, self._extract_commands(result), validation_artifacts=task.validation_artifacts)
        status, reason = self._adjudicate_task(task, verification)
        task.status = status
        task.outcome = getattr(verification, "summary", reason)
        task.verification_results = self._extract_commands(result)
        return task

    @staticmethod
    def format_summary(summary: dict[str, Any]) -> str:
        report = summary.get("report", summary)
        if "tasks_attempted" in report:
            lines = [
                "Villani mode summary",
                f"- done_reason: {report.get('done_reason', '')}",
                f"- blockers: {', '.join(report.get('blockers', []) or []) or 'none'}",
                f"- changed: {report.get('files_changed', [])}",
                "## Villani control loop",
            ]
            memory = dict(report.get("working_memory", {}) or {})
            lines.append(f"- model_requests: {memory.get('model_request_count', 0)}")
            lines.append(f"- stop_reason: {memory.get('stop_decision_kind', report.get('done_reason', ''))}")
            for task in report.get("tasks_attempted", [])[:30]:
                lines.append(f"  * {task.get('title')} [{task.get('status')}] verification={task.get('verification', [])}")
            return "\n".join(lines)
        lines = [
            "Villani mode mission report",
            f"- Mission: {report.get('mission_goal', '')}",
            f"- Type: {report.get('mission_type', '')}",
            f"- Outcome: {report.get('final_outcome', '')}",
            f"- Stop reason: {report.get('stop_reason', '')}",
            f"- Files touched: {', '.join(report.get('files_touched', [])[:12]) or 'none'}",
            "- Node results:",
        ]
        for node in report.get("nodes_executed", [])[:30]:
            outcome = (node.get("last_outcome", {}) or {})
            lines.append(
                f"  * {node.get('title')} [{node.get('status')}] attempts={node.get('attempts')} "
                f"delta={outcome.get('delta_classification', 'n/a')} changed={len(outcome.get('changed_files', []) or [])}"
            )
        timeline = report.get("validation_timeline", []) or []
        if timeline:
            lines.append("- Validation delta timeline:")
            for item in timeline[-12:]:
                lines.append(
                    f"  * {item.get('node_id')}: failed={item.get('failed')} passed={item.get('passed')} "
                    f"delta={item.get('delta')} fp={item.get('fingerprint') or 'none'}"
                )
        if report.get("localization_evolution"):
            lines.append("- Localization evolution:")
            for item in (report.get("localization_evolution", []) or [])[-8:]:
                lines.append(
                    f"  * conf={item.get('confidence'):.2f} bug_class={item.get('bug_class')} "
                    f"targets={', '.join(item.get('targets', [])[:4])}"
                )
        if report.get("greenfield_report"):
            greenfield = dict(report.get("greenfield_report", {}) or {})
            lines.append("- Greenfield selection:")
            lines.append(f"  * direction={greenfield.get('chosen_project_direction', '')}")
            lines.append(f"  * rationale={greenfield.get('selection_rationale', '')}")
            lines.append(f"  * deliverables={', '.join(greenfield.get('user_space_deliverables', [])[:10]) or 'none'}")
        return "\n".join(lines)
