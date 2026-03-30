from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from villani_code.autonomous_progress import (
    mark_category_discovery,
    stop_reason_from_categories,
    surface_followups,
    update_category_attempt_state,
)
from villani_code.autonomy import Opportunity, TakeoverConfig, TakeoverState, TaskContract, VerificationEngine, VerificationStatus
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
    NormalizedNodeOutcome,
    NodePhase,
    NodeStatus,
    NodeOutcomeRecord,
    reduce_normalized_mission_progress,
    reduce_validation_truth,
    select_validation_relevant_commands,
    infer_realized_artifact_direction,
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
    summarize_validation_results,
    VerificationBaseline,
)


def _looks_like_runnable_python(path: str) -> bool:
    low = str(path).strip().lower()
    if not low.endswith(".py"):
        return False
    return not low.startswith(("tests/", "test_", ".villani/", ".villani_code/"))


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
    task_key: str = ""
    attempts: int = 0
    retries: int = 0
    turns_used: int = 0
    tool_calls_used: int = 0
    elapsed_seconds: float = 0.0
    completed: bool = False


class VillaniModeController:
    """Mission-driven autonomous Villani mode that uses the existing runner as execution engine."""

    _GREENFIELD_READ_ONLY_PHASES = {
        NodePhase.INSPECT_WORKSPACE,
        NodePhase.DEFINE_OBJECTIVE,
        NodePhase.SUMMARIZE_OUTCOME,
    }

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
        self._satisfied_task_keys: dict[str, str] = {}
        self._lineage_last_fingerprint: dict[str, str] = {}
        self._lineage_last_intentional_changes: dict[str, tuple[str, ...]] = {}
        self._followup_queue: list[Opportunity] = []
        self._retryable_queue: list[Opportunity] = []
        self._backlog_insertions: list[dict[str, str]] = []
        self._lineage_retry_counts: dict[str, int] = {}
        self._category_state: dict[str, str] = {
            "tests": "unknown",
            "docs": "unknown",
            "entrypoints": "unknown",
            "imports": "unknown",
        }
        self._model_request_count = 0
        self._planner_only_cycles = 0
        self._followup_skip_reasons: list[str] = []

    def _extract_user_space_deliverables(self, changed_files: list[str]) -> list[str]:
        user_deliverables, _internal = split_internal_paths([str(item or "").strip() for item in changed_files])
        return user_deliverables

    def _record_greenfield_progress(
        self,
        execution_state: MissionExecutionState,
        node: Any,
        changed_files: list[str],
        observed_write_paths: list[str],
        execution_payload: dict[str, Any],
        node_status: str,
    ) -> list[str]:
        if execution_state.mission.mission_type != MissionType.GREENFIELD_BUILD:
            return []
        progress = dict(execution_state.greenfield_progress or {})
        verified_writes = [
            str(p).strip()
            for p in list(execution_payload.get("verified_successful_write_paths", []) or [])
            if str(p).strip()
        ]
        verified_inventory = [
            str(p).strip()
            for p in list(execution_payload.get("verified_files_present", []) or [])
            if str(p).strip()
        ]
        blocked_writes = [
            str(p).strip()
            for p in list(execution_payload.get("blocked_write_paths", []) or [])
            if str(p).strip()
        ]
        deliverables = self._extract_user_space_deliverables(verified_writes or changed_files)
        if not deliverables:
            deliverables = self._extract_user_space_deliverables(observed_write_paths)
        current_paths = [str(x) for x in list(progress.get("deliverable_paths", []) or []) if str(x).strip()]
        merged_paths = sorted(dict.fromkeys(current_paths + deliverables))
        progress["deliverable_paths"] = merged_paths
        progress["created_deliverables"] = merged_paths
        progress["verified_files_present"] = sorted(dict.fromkeys(verified_inventory))
        progress["successful_write_paths"] = sorted(
            dict.fromkeys([str(x) for x in list(progress.get("successful_write_paths", []) or []) if str(x).strip()] + verified_writes)
        )
        progress["blocked_write_paths"] = sorted(
            dict.fromkeys([str(x) for x in list(progress.get("blocked_write_paths", []) or []) if str(x).strip()] + blocked_writes)
        )
        expected = [str(x).strip() for x in list(progress.get("expected_files", []) or []) if str(x).strip()]
        if expected:
            progress["missing_expected_files"] = sorted(set(expected) - set(verified_inventory))
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

    def _sync_greenfield_direction_from_artifacts(self, execution_state: MissionExecutionState, deliverables: list[str]) -> None:
        if execution_state.mission.mission_type != MissionType.GREENFIELD_BUILD:
            return
        if not deliverables:
            return
        joined = " ".join(str(x).lower() for x in deliverables)
        inferred = ""
        if "snake" in joined:
            inferred = "snake_cli_game"
        elif "wordguess" in joined or ("word" in joined and "guess" in joined):
            inferred = "word_guessing_game_cli"
        elif "adventure" in joined:
            inferred = "text_adventure_cli"
        elif any(token in joined for token in ("game", "pygame")):
            inferred = "game_cli"
        elif any(token in joined for token in ("cli", "command", "console")):
            inferred = "python_cli_utility"
        if not inferred:
            return
        current = str(execution_state.scratchpad.chosen_project_direction or execution_state.greenfield_selection.get("project_type", "")).strip()
        if inferred != current:
            execution_state.scratchpad.chosen_project_direction = inferred
            execution_state.scratchpad.chosen_product_shape = inferred
            execution_state.greenfield_selection["project_type"] = inferred
            execution_state.mission.mission_context["greenfield_selection"] = dict(execution_state.greenfield_selection)

    def _derive_greenfield_validation_commands(self, execution_state: MissionExecutionState) -> list[str]:
        progress = dict(execution_state.greenfield_progress or {})
        deliverables = [str(x).strip() for x in list(progress.get("deliverable_paths", []) or []) if str(x).strip()]
        python_entries = [p for p in deliverables if _looks_like_runnable_python(p)]
        commands: list[str] = []
        for target in python_entries[:2]:
            commands.append(f"python -m py_compile {target}")
            commands.append(f"python {target} --help")
        if not commands and python_entries:
            commands.append(f"python -m py_compile {python_entries[0]}")
        return list(dict.fromkeys(commands))[:4]

    def _ensure_validate_node_ready(self, execution_state: MissionExecutionState) -> None:
        if execution_state.mission.mission_type != MissionType.GREENFIELD_BUILD:
            return
        validate_nodes = [n for n in execution_state.mission.nodes if n.phase == NodePhase.VALIDATE_PROJECT]
        if not validate_nodes:
            return
        validate_node = validate_nodes[0]
        if validate_node.status in {NodeStatus.SUCCEEDED, NodeStatus.RUNNING}:
            return
        if execution_state.scratchpad.validation_proven:
            return
        commands = self._derive_greenfield_validation_commands(execution_state)
        if commands:
            validate_node.validation_commands = list(dict.fromkeys(commands + list(validate_node.validation_commands)))[:6]
        if execution_state.scratchpad.has_runnable_entrypoint and validate_node.status in {
            NodeStatus.PENDING,
            NodeStatus.FAILED,
            NodeStatus.BLOCKED,
            NodeStatus.EXHAUSTED,
            NodeStatus.SKIPPED,
        }:
            validate_node.status = NodeStatus.READY

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
            selected_direction = str(execution_state.mission.objective.direction or scratchpad.chosen_project_direction).strip()
            if selected_direction:
                scratchpad.chosen_project_direction = selected_direction
            if scratchpad.chosen_project_direction and node.phase == NodePhase.DEFINE_OBJECTIVE:
                node.objective = f"Finalize structured objective direction ({scratchpad.chosen_project_direction}) with bounded scope."
            if scratchpad.confirmed_deliverables and node.phase == NodePhase.INSPECT_WORKSPACE:
                node.status = NodeStatus.SKIPPED
                return
        scratchpad.next_required_action = scratchpad.derive_next_action()

    def _enforce_greenfield_direction_consistency(self, execution_state: MissionExecutionState) -> str:
        objective = execution_state.mission.objective
        scratchpad = execution_state.scratchpad
        selected = str(execution_state.greenfield_selection.get("project_type", "")).strip()
        canonical = str(objective.direction or scratchpad.chosen_project_direction or selected).strip()
        if not canonical:
            return ""
        objective.direction = canonical
        scratchpad.chosen_project_direction = canonical
        scratchpad.chosen_product_shape = canonical
        execution_state.greenfield_selection["project_type"] = canonical
        execution_state.mission.mission_context["greenfield_selection"] = dict(execution_state.greenfield_selection)
        return canonical

    def _synthesize_greenfield_phase_state(self, execution_state: MissionExecutionState, node: Any) -> None:
        if execution_state.mission.mission_type != MissionType.GREENFIELD_BUILD:
            return
        repo_signals = dict(self._repo_signals or execution_state.mission.mission_context.get("repo_signals", {}) or {})
        if node.phase == NodePhase.INSPECT_WORKSPACE:
            findings: list[str] = []
            if repo_signals.get("workspace_empty_or_internal_only"):
                findings.append("workspace empty or internal-only")
            if not repo_signals.get("existing_project_detected", False):
                findings.append("no existing project detected")
            findings.append("greenfield context confirmed")
            hints = list(repo_signals.get("language_hints", []) or [])
            if hints:
                findings.append("language/runtime hints: " + ", ".join(hints[:4]))
            execution_state.mission.mission_context["inspect_workspace_findings"] = findings
            return
        if node.phase != NodePhase.DEFINE_OBJECTIVE:
            return
        objective = execution_state.mission.objective
        direction = self._enforce_greenfield_direction_consistency(execution_state)
        if not direction:
            direction = "python_cli_utility"
            objective.direction = direction
        fallback_repo_state = "unknown"
        if repo_signals.get("workspace_empty_or_internal_only"):
            fallback_repo_state = "empty_sandbox"
        elif repo_signals.get("workspace_lightweight_hints_only"):
            fallback_repo_state = "lightweight_hints"
        elif repo_signals.get("workspace_sparse_greenfield_like"):
            fallback_repo_state = "sparse_scaffold"
        objective.repo_state_type = str(objective.repo_state_type or fallback_repo_state)
        objective.task_shape = str(objective.task_shape or "greenfield_build")
        objective.deliverable_kind = list(objective.deliverable_kind or ["unknown"])
        objective.direction = direction
        objective.initial_validation_strategy = list(objective.initial_validation_strategy or repo_signals.get("likely_validation_commands", []) or ["python -m py_compile <entrypoint>"])[:4]
        execution_state.scratchpad.next_required_action = NodePhase.SCAFFOLD_PROJECT.value
        self._enforce_greenfield_direction_consistency(execution_state)
        execution_state.mission.mission_context["objective"] = {k: getattr(objective, k) for k in objective.__dataclass_fields__.keys()}
        execution_state.mission.mission_context["greenfield_selection"] = dict(execution_state.greenfield_selection)
        execution_state.mission.mission_context["scratchpad"] = execution_state.scratchpad.to_dict()

    def _apply_no_regression_guards(self, execution_state: MissionExecutionState, outcome: dict[str, Any]) -> None:
        scratchpad = execution_state.scratchpad
        mission = execution_state.mission
        if scratchpad.mission_type == MissionType.GREENFIELD_BUILD.value and mission.mission_type != MissionType.GREENFIELD_BUILD:
            mission.mission_type = MissionType.GREENFIELD_BUILD
        self._enforce_greenfield_direction_consistency(execution_state)
        if scratchpad.confirmed_deliverables and not outcome.get("user_deliverable_patch") and outcome.get("status") == "failed":
            outcome["status"] = "partial"
            outcome["reason"] = "no-regression guard: prior deliverables confirmed in scratchpad"

    def _greenfield_completion_gate(self, execution_state: MissionExecutionState) -> dict[str, Any]:
        if execution_state.mission.mission_type != MissionType.GREENFIELD_BUILD:
            return {"ready": False}
        progress = dict(execution_state.greenfield_progress or {})
        deliverables = [str(x) for x in list(progress.get("deliverable_paths", []) or []) if str(x).strip()]
        has_deliverables = bool(deliverables)
        has_entrypoint = bool(execution_state.scratchpad.has_runnable_entrypoint)
        has_validation_evidence = any(
            str(item.get("node_phase", "")) == NodePhase.VALIDATE_PROJECT.value
            and str(item.get("validation_evidence_kind", "")) == "real_command_results"
            for item in execution_state.verification_history
        ) or (
            bool(execution_state.latest_command_results)
            and bool(execution_state.latest_validation_summary.get("commands_run", 0))
        )
        unresolved_critical = bool(progress.get("unresolved_critical_contract_violation", False))
        return {
            "ready": has_deliverables and has_entrypoint and has_validation_evidence and not unresolved_critical,
            "has_deliverables": has_deliverables,
            "has_entrypoint": has_entrypoint,
            "has_validation_evidence": has_validation_evidence,
            "unresolved_critical_contract_violation": unresolved_critical,
        }

    def _promote_greenfield_conclusion(self, execution_state: MissionExecutionState) -> None:
        gate = self._greenfield_completion_gate(execution_state)
        progress = dict(execution_state.greenfield_progress or {})
        if not gate.get("ready"):
            return
        summarize_nodes = [n for n in execution_state.mission.nodes if n.phase == NodePhase.SUMMARIZE_OUTCOME]
        if summarize_nodes:
            summary_node = summarize_nodes[0]
            if summary_node.status in {NodeStatus.PENDING, NodeStatus.FAILED, NodeStatus.READY}:
                summary_node.status = NodeStatus.READY
            append_mission_event(
                str(self.repo),
                execution_state.mission.mission_id,
                {"type": "greenfield_completion_gate_open", "node_id": summary_node.node_id, "gate": gate},
            )
            return
        recovery_nodes = self.planner.spawn_recovery_nodes(
            execution_state.mission,
            execution_state.mission.nodes[-1],
            "advance_summarize",
            "Greenfield completion gate met",
        )
        self.planner.expand_mission_graph(execution_state.mission, recovery_nodes)
        append_mission_event(str(self.repo), execution_state.mission.mission_id, {"type": "greenfield_completion_gate_open", "gate": gate, "spawned_nodes": [n.node_id for n in recovery_nodes]})

    def run(self) -> dict[str, Any]:
        if not (self.steering_objective or "").strip():
            return self._run_legacy_takeover()
        if not hasattr(self.planner, "build_mission"):
            return self._run_legacy_takeover()
        mission_state = self._initialize_mission()
        save_mission_snapshot(str(self.repo), mission_state.mission, mission_state.to_dict())

        while True:
            done = self._mission_done(mission_state)
            if done is not None:
                break
            node = self._select_next_node(mission_state)
            if node is None:
                if mission_state.recovery_nodes_inserted_last > 0:
                    mission_state.recovery_state_note = "Recovery node inserted but no executable ready node was discovered after graph refresh."
                self._activity("Waiting for ready node; incrementing no-progress counter.")
                mission_state.consecutive_no_progress += 1
                continue
            mission_state.recovery_nodes_inserted_last = 0
            result = self._execute_node(mission_state, node)
            outcome = self._evaluate_node(mission_state, node, result)
            self._promote_greenfield_conclusion(mission_state)
            if outcome.get("status") in {"failed", "stale", "partial"}:
                self._handle_recovery(mission_state, node, outcome)

            save_mission_snapshot(str(self.repo), mission_state.mission, mission_state.to_dict())

        return self._finalize_mission(mission_state)

    def _takeover_cfg(self) -> TakeoverConfig:
        if isinstance(self.takeover_config, TakeoverConfig):
            return self.takeover_config
        return TakeoverConfig()

    def _run_legacy_takeover(self) -> dict[str, Any]:
        cfg = self._takeover_cfg()
        preexisting_changes = set(self._git_changed_files())
        attempted: list[AutonomousTask] = []
        state = TakeoverState(
            repo_summary=(
                self.planner.build_repo_summary() if hasattr(self.planner, "build_repo_summary") else "summary"
            )
        )
        mark_category_discovery(self.repo, self._category_state, self._is_meaningful_test_file)
        planner_churn_cycles = 0
        done_reason = ""

        for wave in range(max(1, int(cfg.max_waves))):
            if hasattr(self.planner, "discover_opportunities"):
                discovered = list(self.planner.discover_opportunities())
            else:
                discovered = []
                snapshot = self.inspect_repo()
                discovered = [
                    Opportunity(
                        title=t.title,
                        category="generated",
                        priority=t.priority,
                        confidence=t.confidence,
                        affected_files=[],
                        evidence=t.rationale,
                        blast_radius="small",
                        proposed_next_action=t.title,
                        task_contract=t.task_contract,
                    )
                    for t in self.generate_candidates(snapshot)
                ]
            discovered.extend(self._followup_queue)
            self._followup_queue = []
            discovered.extend(self._retryable_queue)
            self._retryable_queue = []
            ranked = self._build_wave_candidates(discovered)
            if wave == 0 and not discovered and getattr(self.planner, "enable_fallback", True) is False:
                done_reason = "No opportunities discovered."
                break
            if discovered and not ranked:
                _, done_reason = stop_reason_from_categories(self._category_state)
                break
            if not ranked:
                planner_churn_cycles += 1
                self._planner_only_cycles += 1
                if planner_churn_cycles >= 3:
                    self.event_callback({"type": "villani_planner_churn"})
                    done_reason = "Stopped: planner loop with no model activity."
                    break
                rationale, done_reason = stop_reason_from_categories(self._category_state)
                _ = rationale
                continue
            planner_churn_cycles = 0
            op = ranked[0]
            task = AutonomousTask(
                task_id=f"wave-{wave+1}-{len(attempted)+1}",
                title=op.title,
                rationale=op.evidence,
                priority=op.priority,
                confidence=op.confidence,
                verification_plan=[],
                task_contract=op.task_contract,
                task_key=self._task_key_for_opportunity(op),
                attempts=1,
            )
            update_category_attempt_state(self._category_state, task.title)
            task = self._execute_task(task)
            status = self._update_lifecycle_after_attempt(task, op)
            task.status = status
            task.completed = status in {"passed", "exhausted"}
            if status == "passed" and task.task_contract in {TaskContract.VALIDATION.value, TaskContract.VALIDATE.value, TaskContract.INSPECTION.value, TaskContract.INSPECT.value}:
                self._satisfied_task_keys[task.task_key] = self._repo_fingerprint_for_task(task.task_key)
            attempted.append(task)
            state.completed_waves.append({"wave": wave + 1, "title": task.title, "status": task.status})
            if cfg.max_total_task_attempts and len(attempted) >= int(cfg.max_total_task_attempts):
                done_reason = "Villani mode budget exhausted."
                break
            if task.status in {"failed", "retryable", "exhausted"} and task.terminated_reason in {"model_idle", "no_edits"}:
                if cfg.stagnation_cycle_limit and sum(1 for t in attempted[-int(cfg.stagnation_cycle_limit):] if t.terminated_reason in {"model_idle", "no_edits"}) >= int(cfg.stagnation_cycle_limit):
                    done_reason = "No meaningful progress observed across recent cycles."
                    break
            followups = self._deterministic_followups(task, op)
            for followup in followups:
                self._insert_followup(followup, "deterministic")

        if not done_reason:
            _, done_reason = stop_reason_from_categories(self._category_state)

        current_changes = set(self._git_changed_files())
        if not attempted and not done_reason:
            rationale, done_reason = stop_reason_from_categories(self._category_state)
            _ = rationale
        successful = [t for t in attempted if t.status == "passed"]
        failed = [t for t in attempted if t.status in {"failed", "retryable", "exhausted", "blocked"}]
        intentional_changes = sorted({p for t in attempted for p in t.intentional_changes} - preexisting_changes)
        incidental_changes = sorted({p for t in attempted for p in t.incidental_changes} - preexisting_changes)
        files_changed = sorted(current_changes - preexisting_changes)
        return {
            "repo_summary": state.repo_summary,
            "tasks_attempted": [self._task_to_dict(t) for t in attempted],
            "blockers": [t.title for t in attempted if t.status == "blocked"],
            "files_changed": files_changed,
            "preexisting_changes": sorted(preexisting_changes),
            "intentional_changes": intentional_changes,
            "incidental_changes": incidental_changes,
            "opportunities_considered": len(state.completed_waves),
            "opportunities_attempted": len(attempted),
            "successful_tasks": len(successful),
            "failed_tasks": len(failed),
            "completed_waves": state.completed_waves,
            "done_reason": done_reason,
            "recommended_next_steps": ["Run full CI before merging autonomous changes."],
            "working_memory": {
                "model_request_count": self._model_request_count,
                "planner_only_cycles": self._planner_only_cycles,
                "followup_skip_reasons": list(self._followup_skip_reasons),
                "stop_decision_kind": "planner_churn" if "planner loop" in done_reason else ("budget_exhausted" if "budget exhausted" in done_reason else "below_threshold"),
                "satisfied_task_keys": dict(self._satisfied_task_keys),
                "backlog_insertions": list(self._backlog_insertions),
                "category_examination_state": dict(self._category_state),
                "stop_decision_rationale": done_reason,
            },
        }

    def _initialize_mission(self) -> MissionExecutionState:
        self._activity("Initializing mission and collecting repository signals.")
        objective = (self.steering_objective or "").strip()
        self._repo_signals = self._collect_repo_signals()
        mission = self.planner.build_mission(objective, str(self.repo), repo_signals=self._repo_signals)
        mission.mission_context["repo_signals"] = dict(self._repo_signals)
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
        objective_model = self.planner.synthesize_objective(objective, MissionType.GREENFIELD_BUILD, repo_signals=signals)
        kinds = list(objective_model.deliverable_kind or ["unknown"])
        language_hints = list(signals.get("language_hints", []) or [])
        preferred_lang = "python" if "python" in language_hints or "python" in objective.lower() else (language_hints[0] if language_hints else "python")
        candidates: list[dict[str, Any]] = []
        for kind in kinds[:3]:
            project_type = f"{preferred_lang}_{kind}"
            candidates.append({
                "project_type": project_type,
                "utility": 0.85,
                "feasibility": 0.86,
                "fit": 0.8 if kind != "unknown" else 0.6,
                "validation": 0.85,
                "bounded_scope": 0.88,
                "rationale": f"General objective synthesis selected kind={kind} with language={preferred_lang}.",
            })
        if not candidates:
            candidates = [{"project_type": f"{preferred_lang}_unknown", "utility": 0.7, "feasibility": 0.8, "fit": 0.6, "validation": 0.8, "bounded_scope": 0.85, "rationale": "Fallback general-purpose build."}]
        for c in candidates:
            c["score"] = round((0.3 * c["utility"]) + (0.25 * c["feasibility"]) + (0.2 * c["fit"]) + (0.15 * c["validation"]) + (0.1 * c["bounded_scope"]), 3)
        ranked = sorted(candidates, key=lambda item: (-float(item.get("score", 0.0)), str(item.get("project_type", ""))))
        chosen = dict(ranked[0])
        selection = {
            "project_type": chosen.get("project_type", ""),
            "selection_rationale": chosen.get("rationale", ""),
            "objective": {k: getattr(objective_model, k) for k in objective_model.__dataclass_fields__.keys()},
            "constraints": {
                "avoid_internal_deliverables": True,
                "docs_only_invalid_for_open_greenfield": True,
                "target_paths": "workspace_user_paths_only",
                "single_mission_scope": True,
            },
        }
        return ranked[:4], selection


    def _select_next_node(self, execution_state: MissionExecutionState):
        self._activity("Selecting next ready mission node.")
        mission = execution_state.mission
        self._ensure_validate_node_ready(execution_state)
        self._hydrate_nodes_from_localization(execution_state)
        for node in mission.nodes:
            if node.status == NodeStatus.PENDING and all(self._node_by_id(mission, dep).status == NodeStatus.SUCCEEDED for dep in node.depends_on if self._node_by_id(mission, dep)):
                node.status = NodeStatus.READY
        ready_nodes = [n for n in mission.nodes if n.status == NodeStatus.READY]
        if not ready_nodes:
            return None
        if execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD:
            if not execution_state.scratchpad.validation_proven:
                implement_ready = [n for n in ready_nodes if n.phase == NodePhase.IMPLEMENT_INCREMENT]
                validate_ready = [n for n in ready_nodes if n.phase == NodePhase.VALIDATE_PROJECT]
                if validate_ready and not implement_ready:
                    selected = validate_ready[0]
                    self._refresh_scratchpad_pre_node(execution_state, selected)
                    if selected.status == NodeStatus.SKIPPED:
                        return None
                    return selected
            if execution_state.scratchpad.has_runnable_entrypoint and execution_state.scratchpad.has_user_space_scaffolding:
                summarize_ready = [n for n in ready_nodes if n.phase == NodePhase.SUMMARIZE_OUTCOME]
                if summarize_ready:
                    selected = summarize_ready[0]
                    self._refresh_scratchpad_pre_node(execution_state, selected)
                    if selected.status == NodeStatus.SKIPPED:
                        return None
                    return selected
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
        self._synthesize_greenfield_phase_state(execution_state, node)

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
            if node.phase in {NodePhase.SCAFFOLD_PROJECT, NodePhase.IMPLEMENT_INCREMENT} and execution_state.greenfield_selection:
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
        runner_command_results = list(node_result.get("command_results", []) or [])
        command_results: list[dict[str, Any]] = []
        execution_payload = dict(node_result.get("execution_payload", {}) or {})
        payload_command_results = list(execution_payload.get("command_results", []) or [])
        if runner_command_results:
            command_results = list(runner_command_results)
        elif payload_command_results:
            command_results = list(payload_command_results)
        normalized_command_results: dict[str, dict[str, Any]] = {}
        for record in command_results:
            cmd_key = str(record.get("command", "")).strip() or f"__index_{len(normalized_command_results)}"
            normalized_command_results[cmd_key] = dict(record)
        command_results = list(normalized_command_results.values())
        validation_relevant_results = select_validation_relevant_commands(command_results, node_phase=node.phase.value)
        baseline_validation_results = select_validation_relevant_commands(
            list(baseline.previous_command_results),
            node_phase=node.phase.value,
        )
        validation_summary = summarize_validation_results(validation_relevant_results)
        validation_delta = compute_validation_delta(
            baseline.validation_summary,
            baseline_validation_results,
            validation_relevant_results,
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
            mission_objective={
                "repo_state_type": execution_state.mission.objective.repo_state_type,
                "task_shape": execution_state.mission.objective.task_shape,
                "deliverable_kind": list(execution_state.mission.objective.deliverable_kind or []),
                "direction": execution_state.mission.objective.direction,
                "initial_validation_strategy": list(execution_state.mission.objective.initial_validation_strategy or []),
            },
        )
        attempted_write_paths = [str(p) for p in list(execution_payload.get("attempted_write_paths", []) or []) if str(p).strip()]
        observed_write_paths = [str(p) for p in list(execution_payload.get("observed_write_paths", []) or []) if str(p).strip()]
        verified_successful_write_paths = [
            str(p) for p in list(execution_payload.get("verified_successful_write_paths", []) or []) if str(p).strip()
        ]
        blocked_write_paths = [str(p) for p in list(execution_payload.get("blocked_write_paths", []) or []) if str(p).strip()]
        rejected_actions = list(execution_payload.get("rejected_actions", []) or [])
        if rejected_actions:
            outcome["contract_violation"] = True
            outcome["rejected_actions"] = rejected_actions

        shell_invocations = [str(c) for c in list(execution_payload.get("shell_invocations", []) or []) if str(c).strip()]
        inferred_command_results = list(execution_payload.get("inferred_command_results", []) or [])
        villani_unrestricted_mode = bool(getattr(self.runner, "villani_unrestricted_mode", False))
        if (
            not villani_unrestricted_mode
            and execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD
            and node.phase == NodePhase.SUMMARIZE_OUTCOME
        ):
            if changed_files or attempted_write_paths or shell_invocations:
                outcome["status"] = "failed"
                outcome["reason"] = "contract violation: summarize_outcome is read-only and cannot write/execute build actions"
                outcome["contract_violation"] = True
                outcome["phase_contract_status"] = "contract_violation"
        if (
            not villani_unrestricted_mode
            and execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD
            and node.phase in self._GREENFIELD_READ_ONLY_PHASES
            and changed_files
        ):
            outcome["status"] = "failed"
            outcome["reason"] = f"contract violation: {node.phase.value} is read-only but wrote files"
            outcome["contract_violation"] = True
        if not villani_unrestricted_mode and node.phase in self._GREENFIELD_READ_ONLY_PHASES and blocked_write_paths and not changed_files:
            outcome["status"] = "passed" if str(outcome.get("status")) in {"passed", "partial", "failed"} else "partial"
            outcome["reason"] = "recoverable contract violation: blocked write attempt in read-only phase; proceeding with preserved mission state"
            outcome["contract_violation"] = True
            outcome["contract_violation_recovered"] = True
            outcome["phase_contract_status"] = "contract_violation_recovered"
        elif bool(outcome.get("contract_violation")):
            outcome["contract_violation_recovered"] = False

        effective_changed_files = list(changed_files)
        if node.phase == NodePhase.SUMMARIZE_OUTCOME and bool(outcome.get("contract_violation")):
            effective_changed_files = []
        user_deliverables = self._extract_user_space_deliverables(effective_changed_files)
        observed_user_deliverables = self._extract_user_space_deliverables(verified_successful_write_paths or observed_write_paths)
        effective_user_deliverables = list(user_deliverables or observed_user_deliverables)
        if (
            execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD
            and node.phase == NodePhase.SCAFFOLD_PROJECT
            and effective_user_deliverables
        ):
            outcome["status"] = "passed"
            outcome["reason"] = "greenfield scaffold created user-space deliverables"
            outcome["patch_no_improvement"] = False
            outcome["validation_worsened"] = False
        if (
            execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD
            and node.phase == NodePhase.IMPLEMENT_INCREMENT
            and effective_user_deliverables
            and str(outcome.get("status", "")) not in {"failed"}
        ):
            outcome["status"] = "passed"
            outcome["reason"] = "greenfield vertical slice created runnable user-space artifact; validation deferred to validate_project"
            outcome["patch_no_improvement"] = False
            outcome["validation_worsened"] = False

        failure_fingerprint = validation_summary.get("failure_fingerprints", [""])[0] if validation_summary.get("failure_fingerprints") else ""
        if failure_fingerprint:
            node.failure_fingerprint = failure_fingerprint
            execution_state.failure_fingerprint_history.append(failure_fingerprint)

        node.last_outcome = NodeOutcomeRecord(
            status=str(outcome.get("status", "unknown")),
            phase_contract_status=str(outcome.get("phase_contract_status", "unknown")),
            mission_progress_status=str(outcome.get("mission_progress_status", "no_progress")),
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
            effective_changed_files,
            observed_write_paths,
            execution_payload,
            str(outcome.get("status", "")),
        )
        self._sync_greenfield_direction_from_artifacts(execution_state, persisted_deliverables)
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
                (node.phase in {NodePhase.IMPLEMENT_INCREMENT, NodePhase.VALIDATE_PROJECT} and str(outcome.get("status", "")) in {"passed", "partial"})
                or any(_looks_like_runnable_python(p) for p in persisted_deliverables)
            )
            progress = dict(execution_state.greenfield_progress or {})
            critical_violation = bool(outcome.get("contract_violation")) and not bool(outcome.get("contract_violation_recovered")) and not bool(
                outcome.get("user_deliverable_patch") and node.phase in self._GREENFIELD_READ_ONLY_PHASES
            )
            if bool(outcome.get("contract_violation")):
                progress["last_contract_violation_phase"] = node.phase.value
            progress["unresolved_critical_contract_violation"] = bool(
                progress.get("unresolved_critical_contract_violation", False) or critical_violation
            )
            execution_state.greenfield_progress = progress
            execution_state.mission.mission_context["greenfield_progress"] = dict(progress)
            self._ensure_validate_node_ready(execution_state)
        self._apply_no_regression_guards(execution_state, outcome)
        execution_state.mission.mission_context["scratchpad"] = execution_state.scratchpad.to_dict()
        node_validation_truth, node_validation_summary = reduce_validation_truth(command_results, node_phase=node.phase.value)
        node_realized_direction = infer_realized_artifact_direction(
            persisted_deliverables or user_deliverables,
            fallback=execution_state.scratchpad.chosen_project_direction,
        )
        blocked_reason = (
            str(outcome.get("reason", ""))
            if (
                outcome.get("mission_progress_status") == "blocked"
                or (blocked_write_paths and not effective_user_deliverables)
            )
            else ""
        )
        normalized = NormalizedNodeOutcome(
            node_id=node.node_id,
            node_phase=node.phase.value,
            contract_status=str(outcome.get("phase_contract_status", "unknown")),
            mission_progress_status=str(outcome.get("mission_progress_status", "no_progress")),
            successful_user_writes=list(effective_user_deliverables),
            blocked_write_attempts=list(blocked_write_paths),
            internal_artifact_writes=list(internal_changed_files),
            actual_changed_files_count=len(effective_user_deliverables),
            deliverable_paths=list(effective_user_deliverables),
            command_results=list(command_results),
            validation_truth_status=node_validation_truth,
            validation_summary=node_validation_summary,
            realized_artifact_direction=node_realized_direction,
            next_recommended_action=execution_state.scratchpad.derive_next_action(),
            terminal_candidate_state=str(outcome.get("status", "")),
            blocked_reason=blocked_reason,
        )
        execution_state.normalized_node_outcomes.append(normalized)

        node.evidence.extend(static_result.get("findings", []))
        node.evidence.extend([f"cmd:{r.get('command')} exit={r.get('exit')}" for r in command_results])
        execution_state.verification_history.append(
            {
                "node_id": node.node_id,
                "node_phase": node.phase.value,
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
                "attempted_write_paths": attempted_write_paths,
                "blocked_write_paths": blocked_write_paths,
                "self_reported_validation_without_evidence": bool(outcome.get("self_reported_validation_without_evidence")),
                "validation_evidence_kind": (
                    "real_command_results"
                    if command_results
                    else ("inferred_non_authoritative" if inferred_command_results else "none")
                ),
                "inferred_command_results": inferred_command_results,
                "verification_status": str(outcome.get("verification_status", "validation_unproven")),
                "normalized_outcome": {
                    "node_id": normalized.node_id,
                    "node_phase": normalized.node_phase,
                    "contract_status": normalized.contract_status,
                    "mission_progress_status": normalized.mission_progress_status,
                    "successful_user_writes": normalized.successful_user_writes,
                    "blocked_write_attempts": normalized.blocked_write_attempts,
                    "internal_artifact_writes": normalized.internal_artifact_writes,
                    "actual_changed_files_count": normalized.actual_changed_files_count,
                    "deliverable_paths": normalized.deliverable_paths,
                    "validation_truth_status": normalized.validation_truth_status,
                    "validation_summary": normalized.validation_summary,
                    "realized_artifact_direction": normalized.realized_artifact_direction,
                    "next_recommended_action": normalized.next_recommended_action,
                    "terminal_candidate_state": normalized.terminal_candidate_state,
                    "blocked_reason": normalized.blocked_reason,
                },
            }
        )

        read_only_planning_phase = node.phase in {NodePhase.INSPECT_WORKSPACE, NodePhase.DEFINE_OBJECTIVE}
        non_failed_read_only_partial = (
            read_only_planning_phase
            and str(outcome.get("status", "")) == "partial"
            and str(outcome.get("mission_progress_status", "")) in {"state_progress", "state_progress_partial"}
        )
        recovered_contract_violation = bool(outcome.get("contract_violation_recovered"))

        if outcome["status"] == "passed" or recovered_contract_violation or non_failed_read_only_partial:
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
        if outcome.get("contract_violation"):
            append_mission_event(
                str(self.repo),
                execution_state.mission.mission_id,
                {
                    "type": "contract_violation",
                    "node_id": node.node_id,
                    "phase": node.phase.value,
                    "reason": outcome.get("reason", ""),
                    "changed_files": changed_files,
                },
            )
        return {
            **outcome,
            "changed_files": changed_files,
            "internal_changed_files": internal_changed_files,
            "patch_detected": bool(outcome.get("patch_exists")),
            "meaningful_patch": bool(outcome.get("meaningful_patch")),
            "validation_summary": validation_summary,
            "failure_fingerprint": failure_fingerprint,
            "localization_evidence": list(localization_payload.get("evidence", [])),
            "outcome_semantic": ("contract_violation_recovered" if outcome.get("contract_violation_recovered") else ("contract_violation_unrecovered" if outcome.get("contract_violation") else str(outcome.get("status", "partial")))),
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
        execution_state.recovery_nodes_inserted_last = 0
        execution_state.recovery_state_note = ""
        outcome["authoritative_direction"] = execution_state.scratchpad.chosen_project_direction
        outcome["ignored_internal_paths"] = list(execution_state.scratchpad.ignored_internal_paths)
        failed_commands = [str(cmd.get("command", "")) for cmd in list(outcome.get("validation_summary", {}).get("failed_commands", [])) if isinstance(cmd, dict)]
        failed_blob = "\n".join(failed_commands).lower()
        if node.phase == NodePhase.VALIDATE_PROJECT and any(tok in failed_blob for tok in {"unicodeencodeerror", "encoding", "cp1252", "emoji"}):
            outcome["repair_focus"] = (
                "Patch console/text output to be encoding-safe on Windows (avoid raw emoji-only output), "
                "then rerun targeted validate_project commands."
            )
        if execution_state.mission.mission_type == MissionType.GREENFIELD_BUILD:
            gate = self._greenfield_completion_gate(execution_state)
            if gate.get("ready"):
                append_mission_event(
                    str(self.repo),
                    execution_state.mission.mission_id,
                    {"type": "recovery_suppressed", "node_id": node.node_id, "reason": "greenfield completion gate satisfied", "gate": gate},
                )
                return
            if execution_state.scratchpad.has_runnable_entrypoint and execution_state.scratchpad.has_user_space_scaffolding and node.phase == NodePhase.VALIDATE_PROJECT:
                append_mission_event(
                    str(self.repo),
                    execution_state.mission.mission_id,
                    {
                        "type": "recovery_suppressed",
                        "node_id": node.node_id,
                        "reason": "validation did not conclude but runnable artifact exists; converge to summary",
                    },
                )
                self._promote_greenfield_conclusion(execution_state)
                return
            progress = dict(execution_state.greenfield_progress or {})
            has_scaffold_success = bool(progress.get("successful_greenfield_scaffold"))
            salvageable_contract_violation = bool(outcome.get("contract_violation")) and bool(outcome.get("user_deliverable_patch"))
            if salvageable_contract_violation:
                strategy = "advance_validate" if execution_state.scratchpad.has_runnable_entrypoint else "rescope"
                nodes = self.planner.spawn_recovery_nodes(
                    execution_state.mission,
                    node,
                    strategy,
                    "salvage deliverables created during read-only phase contract violation",
                )
                self.planner.expand_mission_graph(execution_state.mission, nodes)
                self._refresh_after_recovery_insertion(execution_state, nodes)
                append_mission_event(
                    str(self.repo),
                    execution_state.mission.mission_id,
                    {"type": "contract_violation_salvaged", "node_id": node.node_id, "strategy": strategy, "spawned_nodes": [n.node_id for n in nodes]},
                )
                return
            if has_scaffold_success and node.phase in {
                NodePhase.INSPECT_WORKSPACE,
                NodePhase.DEFINE_OBJECTIVE,
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
            execution_state.recovery_state_note = f"Recovery branch not created: {decision.reason}"
        elif decision.mark_exhausted:
            node.status = NodeStatus.EXHAUSTED
            execution_state.recovery_state_note = f"Recovery branch exhausted before insertion: {decision.reason}"
        else:
            if not decision.nodes:
                execution_state.recovery_state_note = f"Recovery planning failed: strategy '{decision.strategy}' produced no runnable nodes."
                append_mission_event(
                    str(self.repo),
                    execution_state.mission.mission_id,
                    {
                        "type": "recovery_creation_failed",
                        "node_id": node.node_id,
                        "strategy": decision.strategy,
                        "reason": execution_state.recovery_state_note,
                    },
                )
                return
            self.planner.expand_mission_graph(execution_state.mission, decision.nodes)
            self._refresh_after_recovery_insertion(execution_state, decision.nodes)
        append_mission_event(str(self.repo), execution_state.mission.mission_id, {"type": "recovery", "node_id": node.node_id, "strategy": decision.strategy, "reason": decision.reason, "spawned_nodes": [n.node_id for n in decision.nodes]})

    def _refresh_after_recovery_insertion(self, execution_state: MissionExecutionState, nodes: list[Any]) -> None:
        inserted_ids = {n.node_id for n in nodes}
        mission = execution_state.mission
        for inserted in nodes:
            if inserted.status != NodeStatus.READY:
                deps = [self._node_by_id(mission, dep) for dep in inserted.depends_on]
                if all(dep is not None and dep.status == NodeStatus.SUCCEEDED for dep in deps):
                    inserted.status = NodeStatus.READY
        execution_state.recovery_nodes_inserted_last = len(inserted_ids)
        execution_state.consecutive_no_progress = 0
        ready_inserted = [n for n in mission.nodes if n.node_id in inserted_ids and n.status == NodeStatus.READY]
        if not ready_inserted:
            execution_state.recovery_state_note = "Recovery node inserted but dependencies are unsatisfied or node was filtered from READY selection."
        else:
            execution_state.recovery_state_note = ""

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
        normalized_progress = reduce_normalized_mission_progress(execution_state)
        touched = list(normalized_progress.files_touched)
        internal_touched = list(normalized_progress.internal_artifact_writes)
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

    @staticmethod
    def _task_to_dict(task: AutonomousTask) -> dict[str, Any]:
        return {
            "id": task.task_id,
            "title": task.title,
            "status": task.status,
            "task_contract": task.task_contract,
            "attempts": task.attempts,
            "retries": task.retries,
            "reason": task.outcome,
            "verification": task.verification_results,
            "validation_artifacts": task.validation_artifacts,
            "inspection_summary": task.inspection_summary,
            "runner_failures": task.runner_failures,
            "produced_effect": task.produced_effect,
            "produced_validation": task.produced_validation,
            "produced_inspection_conclusion": task.produced_inspection_conclusion,
            "files_changed": task.files_changed,
            "intentional_changes": task.intentional_changes,
            "incidental_changes": task.incidental_changes,
            "terminated_reason": task.terminated_reason,
            "turns_used": task.turns_used,
            "tool_calls_used": task.tool_calls_used,
            "elapsed_seconds": task.elapsed_seconds,
            "completed": task.completed,
        }

    @staticmethod
    def _task_slug(title: str) -> str:
        return "-".join(str(title).strip().lower().replace("/", " ").split())

    def _task_key_for_opportunity(self, op: Opportunity) -> str:
        return self._task_slug(op.title)

    def _repo_fingerprint_for_task(self, task_key: str) -> str:
        stamp_parts: list[str] = []
        for path in sorted(p for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.parts):
            rel = path.relative_to(self.repo).as_posix()
            if is_internal_villani_path(rel):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                stamp_parts.append(f"{rel}:{len(text)}:{hash(text)}")
            except OSError:
                continue
            if len(stamp_parts) >= 150:
                break
        return f"{task_key}|{'|'.join(stamp_parts)}"

    def _is_task_satisfied(self, task_key: str) -> bool:
        previous = self._satisfied_task_keys.get(task_key)
        if not previous:
            return False
        return previous == self._repo_fingerprint_for_task(task_key)

    @staticmethod
    def _is_meaningful_test_file(path: str) -> bool:
        normalized = str(path).replace("\\", "/")
        return normalized.startswith("tests/test_") and normalized.endswith(".py")

    @staticmethod
    def _split_changes(changed_files: list[str]) -> tuple[list[str], list[str], list[str]]:
        all_changes = [str(item).strip() for item in (changed_files or []) if str(item).strip()]
        def _incidental(path: str) -> bool:
            low = path.replace("\\", "/")
            return is_internal_villani_path(path) or "__pycache__/" in low or low.endswith(".pyc")
        intentional = [p for p in all_changes if not _incidental(p)]
        incidental = [p for p in all_changes if _incidental(p)]
        return sorted(dict.fromkeys(intentional)), sorted(dict.fromkeys(incidental)), sorted(dict.fromkeys(all_changes))

    def _build_wave_candidates(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        cfg = self._takeover_cfg()
        out: list[Opportunity] = []
        for op in opportunities:
            if float(op.confidence) < float(cfg.min_confidence):
                self._followup_skip_reasons.append("below_threshold")
                continue
            key = self._task_key_for_opportunity(op)
            if self._is_task_satisfied(key):
                self._followup_skip_reasons.append("already_satisfied")
                continue
            out.append(op)
        return sorted(out, key=lambda op: (1 if op.category.startswith("followup") else 0, op.priority, op.confidence), reverse=True)

    def _insert_followup(self, op: Opportunity, source: str) -> None:
        key = self._task_key_for_opportunity(op)
        if any(self._task_key_for_opportunity(existing) == key for existing in self._followup_queue):
            return
        self._followup_queue.append(op)
        self._backlog_insertions.append({"title": op.title, "source": source})

    def _deterministic_followups(self, task: AutonomousTask | Any, op: Opportunity) -> list[Opportunity]:
        if str(getattr(task, "status", "")) in {"failed", "retryable"} and self._is_stale_repeat(task):
            return []
        followups: list[Opportunity] = []
        if getattr(task, "status", "") == "passed":
            category_snapshot = dict(self._category_state)
            tests_present = any(self._is_meaningful_test_file(p.relative_to(self.repo).as_posix()) for p in self.repo.rglob("tests/test_*.py"))
            docs_present = (self.repo / "README.md").exists() or (self.repo / "docs").exists()
            cli_present = any(p.name == "cli.py" for p in self.repo.rglob("*.py"))
            if not tests_present:
                category_snapshot["tests"] = "unknown"
            if not docs_present:
                category_snapshot["docs"] = "unknown"
            if not cli_present:
                category_snapshot["entrypoints"] = "unknown"
            followups.extend(surface_followups(category_snapshot))
        title = str(getattr(task, "title", "")).lower()
        changed = list(getattr(task, "intentional_changes", []) or [])
        if changed and not getattr(task, "produced_validation", False):
            followups.append(
                Opportunity(
                    "Validate recent autonomous changes",
                    "followup_validation",
                    0.98,
                    0.88,
                    changed[:4],
                    "post-edit validation required",
                    "small",
                    "run targeted validation",
                    TaskContract.VALIDATION.value,
                )
            )
        if "bootstrap minimal tests" in title:
            followups.append(
                Opportunity(
                    "Complete baseline tests scaffolding",
                    "followup_tests_complete",
                    0.95,
                    0.8,
                    [],
                    "initial bootstrap may be partial",
                    "small",
                    "complete baseline tests scaffolding",
                    TaskContract.EFFECTFUL.value,
                )
            )
        if "validate baseline importability" in title and not bool(getattr(task, "produced_validation", True)):
            followups.append(
                Opportunity(
                    "Re-run Validate baseline importability validation",
                    "followup_validation",
                    0.97,
                    0.85,
                    [],
                    "missing validation evidence",
                    "small",
                    "rerun importability validation with command evidence",
                    TaskContract.VALIDATION.value,
                )
            )
        return followups

    def _is_stale_repeat(self, task: Any) -> bool:
        key = str(getattr(task, "task_key", "")).strip()
        if not key:
            return False
        prior_fp = self._lineage_last_fingerprint.get(key)
        prior_changes = self._lineage_last_intentional_changes.get(key)
        current_fp = self._repo_fingerprint_for_task(key)
        current_changes = tuple(sorted(getattr(task, "intentional_changes", []) or []))
        return bool(getattr(task, "attempts", 0) >= 2 and prior_fp == current_fp and prior_changes == current_changes)

    def _update_lifecycle_after_attempt(self, task: AutonomousTask, op: Opportunity) -> str:
        key = str(task.task_key or self._task_key_for_opportunity(op))
        task.task_key = key
        self._lineage_last_fingerprint[key] = self._repo_fingerprint_for_task(key)
        self._lineage_last_intentional_changes[key] = tuple(sorted(task.intentional_changes))
        if task.status == "passed":
            return "passed"
        if self._is_stale_repeat(task):
            return "exhausted"
        if task.terminated_reason in {"model_idle", "no_edits"}:
            retry_count = self._lineage_retry_counts.get(key, 0) + 1
            self._lineage_retry_counts[key] = retry_count
            task.retries = retry_count
            return "retryable" if retry_count <= 1 else "exhausted"
        return "failed"

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
            if not self._has_real_validation_artifact(task):
                return "failed", "validation_not_executed"
            if getattr(verification, "status", None) == VerificationStatus.UNCERTAIN:
                return "failed", "verification_uncertain"
            return "passed", "validation_satisfied"
        if contract in {TaskContract.INSPECTION.value, TaskContract.INSPECT.value}:
            if getattr(verification, "status", None) == VerificationStatus.UNCERTAIN:
                return "failed", "verification_uncertain"
            if task.produced_inspection_conclusion and task.inspection_summary.strip():
                return "passed", "inspection_completed"
            return "failed", "inspection_incomplete"
        if contract in {TaskContract.EFFECTFUL.value, TaskContract.NARROW_FIX.value, TaskContract.BROAD_FIX.value, TaskContract.IMPLEMENT.value, TaskContract.CLEANUP.value}:
            if getattr(verification, "status", None) == VerificationStatus.UNCERTAIN:
                return "failed", "verification_uncertain"
            title = task.title.lower()
            if "bootstrap minimal tests" in title and not any(str(path).replace("\\", "/").startswith("tests/") for path in task.intentional_changes):
                return "failed", "bootstrap_requires_test_file_change"
            if task.produced_effect and bool(task.intentional_changes):
                return "passed", "effectful_change_detected"
            return "failed", "no_effectful_change"
        return "failed", "contract_not_satisfied"

    def _execute_task(self, task: AutonomousTask) -> AutonomousTask:
        self.event_callback({"type": "villani_model_request_started", "task_id": task.task_id, "title": task.title})
        validation_plan = list(task.verification_plan[:3])
        if not validation_plan and "validate baseline importability" in task.title.lower():
            validation_plan = ["python -c 'import villani_code'"]
        prompt = f"Task: {task.title}\nReason: {task.rationale}\nNo network. Keep scope narrow.\nValidation plan: {'; '.join(validation_plan)}"
        if task.title == "Inspect repo for highest-leverage improvement":
            prompt += (
                "\nInspection checklist:\n"
                "1) top-level README.md or README.rst\n"
                "2) pyproject.toml / setup.cfg / requirements files\n"
                "3) tests/ coverage surface and obvious gaps\n"
                "4) up to 3 representative Python source files\n"
            )
        self._model_request_count += 1
        result = self.runner.run(prompt, execution_budget=None)
        self.event_callback({"type": "villani_model_request_finished", "task_id": task.task_id, "title": task.title})

        execution = (result or {}).get("execution", {}) if isinstance(result, dict) else {}
        task.terminated_reason = str(execution.get("terminated_reason", ""))
        task.turns_used = int(execution.get("turns_used", 0) or 0)
        task.tool_calls_used = int(execution.get("tool_calls_used", 0) or 0)
        task.elapsed_seconds = float(execution.get("elapsed_seconds", 0.0) or 0.0)
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
        if status != "passed" and getattr(verification, "findings", None):
            task.outcome = str(verification.findings[0].message)
        else:
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
                f"- incidental_changed: {report.get('incidental_changes', [])}",
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
        verification_timeline = report.get("verification_status_timeline", []) or []
        if verification_timeline:
            lines.append("- Verification status timeline (command-evidence authoritative):")
            for item in verification_timeline[-12:]:
                lines.append(f"  * {item.get('node_id')}: {item.get('verification_status', 'validation_unproven')}")
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
            lines.append(f"  * validation_state={greenfield.get('validation_state', 'unproven')}")
            lines.append(f"  * mission_completion_state={greenfield.get('mission_completion_state', 'partial')}")
            lines.append(f"  * remaining_next_action={greenfield.get('remaining_next_action', '')}")
            write_accounting = dict(greenfield.get("write_accounting", {}) or {})
            blocked = ", ".join(write_accounting.get("attempted_but_blocked_only", [])[:8]) or "none"
            lines.append(f"  * blocked_write_attempts={blocked}")
        truth = str(report.get("validation_truth_statement", "")).strip()
        if truth:
            lines.append(f"- Validation truth: {truth}")
        return "\n".join(lines)
