from __future__ import annotations

import json
import hashlib
import sys
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from villani_code.autonomy import (
    FailureCategory,
    FailureClassifier,
    Opportunity,
    TakeoverConfig,
    TakeoverPlanner,
    TakeoverState,
    TaskContract,
    VerificationEngine,
    VerificationStatus,
)
from villani_code.autonomous_stop import DoneReason, StopDecision
from villani_code.shells import (
    baseline_import_validation_command,
    classify_shell_portability_failure,
    shell_family_for_platform,
)
from villani_code.runtime_safety import ensure_runtime_dependencies_not_shadowed
from villani_code.repo_rules import (
    classify_repo_path,
    is_ignored_repo_path,
)


@dataclass(slots=True)
class VillaniModeConfig:
    enabled: bool = False
    steering_objective: str | None = None


@dataclass(slots=True)
class RepoSnapshot:
    key_files: list[str]
    docs: list[str]
    tests: list[str]
    config_files: list[str]
    ci_files: list[str]
    tooling_commands: list[str]
    todo_hits: list[str]


class TaskLifecycle(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    RETRYABLE = "retryable"
    EXHAUSTED = "exhausted"


@dataclass(slots=True)
class AutonomousTask:
    task_id: str
    title: str
    rationale: str
    priority: float
    confidence: float
    verification_plan: list[str]
    task_contract: str = TaskContract.INSPECTION.value
    task_key: str = ""
    parent_task_key: str = ""
    origin_kind: str = "discovery"
    attempts: int = 0
    retries: int = 0
    status: str = TaskLifecycle.PENDING.value
    outcome: str = ""
    files_changed: list[str] = field(default_factory=list)
    intentional_changes: list[str] = field(default_factory=list)
    incidental_changes: list[str] = field(default_factory=list)
    intended_targets: list[str] = field(default_factory=list)
    before_contents: dict[str, str] = field(default_factory=dict)
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    validation_artifacts: list[str] = field(default_factory=list)
    inspection_summary: str = ""
    runner_failures: list[str] = field(default_factory=list)
    produced_effect: bool = False
    produced_validation: bool = False
    produced_inspection_conclusion: bool = False
    terminated_reason: str = ""
    turns_used: int = 0
    tool_calls_used: int = 0
    elapsed_seconds: float = 0.0
    completed: bool = False


class VillaniModeController:
    """Deterministic autonomous repo-improvement loop for Villani mode."""

    def __init__(
        self,
        runner: Any,
        repo: Path,
        steering_objective: str | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        takeover_config: TakeoverConfig | None = None,
    ) -> None:
        self.runner = runner
        self.repo = repo.resolve()
        ensure_runtime_dependencies_not_shadowed(self.repo)
        self.steering_objective = steering_objective
        self.event_callback = event_callback or (lambda _event: None)
        self.takeover_config = takeover_config or TakeoverConfig()
        self.attempted: list[AutonomousTask] = []
        self._lineage_attempts: dict[str, int] = {}
        self._lineage_retries: dict[str, int] = {}
        self._lineage_status: dict[str, str] = {}
        self._lineage_blockers: dict[str, str] = {}
        self._retryable_queue: list[Opportunity] = []
        self._followup_queue: list[Opportunity] = []
        self._attempt_counter: int = 0
        self.planner = TakeoverPlanner(self.repo)
        self.verifier = VerificationEngine(self.repo, logger=self._log)
        self.failure_classifier = FailureClassifier()
        self._preexisting_changes: set[str] = set()
        self._satisfied_task_keys: dict[str, str] = {}
        self._invalidated_task_keys: set[str] = set()
        self._backlog_insertions: list[dict[str, Any]] = []
        self._filtered_reasons_by_category: dict[str, str] = {}
        self._category_state: dict[str, str] = {
            "tests": "unknown",
            "docs": "unknown",
            "entrypoints": "unknown",
            "imports": "unknown",
        }
        self._stop_rationale: dict[str, str] = {}
        self._stale_task_keys: set[str] = set()
        self._critic_outcomes: list[str] = []
        self._model_request_count: int = 0
        self._planner_only_cycles: int = 0
        self._last_model_request_count: int = 0
        self._stop_decision_kind: StopDecision | None = None
        self._followup_skip_reasons: list[dict[str, str]] = []
        self._lineage_terminal_fingerprint: dict[str, str] = {}
        self._lineage_last_fingerprint: dict[str, str] = {}
        self._lineage_last_intentional_changes: dict[str, tuple[str, ...]] = {}
        self._wave_had_model_activity: bool = False
        self._opportunities_considered: int = 0
        self._opportunities_attempted: int = 0

    def run(self) -> dict[str, Any]:
        self._preexisting_changes = set(self._git_changed_files())
        state = TakeoverState(repo_summary=self.planner.build_repo_summary())
        self._emit(
            "takeover_dashboard",
            summary=state.repo_summary,
            wave=0,
            risk=state.current_risk_level,
        )

        wave = 0
        stagnation_cycles = 0
        prior_changed_snapshot = set(self._git_changed_files())

        while True:
            wave += 1
            self._wave_had_model_activity = False
            if self.takeover_config.max_waves > 0 and wave > self.takeover_config.max_waves:
                if self._has_pending_actionable_work():
                    return self._finalize_stop(state, StopDecision.BUDGET_EXHAUSTED, DoneReason.BUDGET_EXHAUSTED)
                return self._finalize_stop(state, StopDecision.BELOW_THRESHOLD, self._stop_reason_from_categories())
            if (
                self.takeover_config.max_total_task_attempts > 0
                and self._attempt_counter >= self.takeover_config.max_total_task_attempts
            ):
                return self._finalize_stop(state, StopDecision.BUDGET_EXHAUSTED, DoneReason.BUDGET_EXHAUSTED)

            state.repo_summary = self.planner.build_repo_summary()
            discovered = self.planner.discover_opportunities()
            state.discovered_opportunities = discovered
            self._mark_category_discovery()
            self._log(
                f"[villani-mode] explorer found tests/docs/entrypoints; backlog now has {len(discovered)} candidates"
            )

            candidates = self._build_wave_candidates(discovered)
            self._emit(
                "takeover_ranked",
                count=len(candidates),
                top=[o.title for o in candidates[:5]],
            )

            if not candidates:
                if (
                    not discovered
                    and not self._retryable_queue
                    and not self._followup_queue
                    and getattr(self.planner, "enable_fallback", True) is False
                ):
                    return self._finalize_stop(
                        state, StopDecision.NO_OPPORTUNITIES, DoneReason.NO_OPPORTUNITIES
                    )
                self._planner_only_cycles += 1
                self._emit(
                    "villani_planner_churn",
                    wave=wave,
                    planner_only_cycles=self._planner_only_cycles,
                    model_request_count=self._model_request_count,
                    repo_delta=False,
                )
                if self._planner_only_cycles >= 2:
                    reason = DoneReason.PLANNER_CHURN
                    self._stop_rationale["planner_churn"] = reason
                    return self._finalize_stop(state, StopDecision.PLANNER_CHURN, reason)
                if self._enqueue_surface_followups_if_needed():
                    continue
                if (
                    not discovered
                    and not self._retryable_queue
                    and not self._followup_queue
                    and getattr(self.planner, "enable_fallback", True) is False
                ):
                    return self._finalize_stop(
                        state, StopDecision.NO_OPPORTUNITIES, DoneReason.NO_OPPORTUNITIES
                    )
                if self._has_pending_actionable_work():
                    return self._finalize_stop(
                        state, StopDecision.BUDGET_EXHAUSTED, "Retry budget exhausted for remaining work."
                    )
                if discovered:
                    return self._finalize_stop(
                        state, StopDecision.BELOW_THRESHOLD, self._stop_reason_from_categories()
                    )
                if self._planner_only_cycles < 2:
                    continue
                return self._finalize_stop(state, StopDecision.BELOW_THRESHOLD, self._stop_reason_from_categories())

            if self.takeover_config.max_commands_per_wave > 0:
                selected = candidates[: self.takeover_config.max_commands_per_wave]
            else:
                selected = candidates
            self._opportunities_considered += len(candidates)
            self._opportunities_attempted += len(selected)
            self._emit("autonomous_phase", phase=f"Villani mode wave {wave}")
            self._emit(
                "takeover_wave",
                wave=wave,
                selected=[o.title for o in selected],
                why="ranked by priority and confidence",
            )

            wave_files: set[str] = set()
            retired = 0
            retryable = 0
            blocked = 0
            for index, op in enumerate(selected, start=1):
                if (
                    self.takeover_config.max_total_task_attempts > 0
                    and self._attempt_counter >= self.takeover_config.max_total_task_attempts
                ):
                    return self._finalize_stop(
                        state, StopDecision.BUDGET_EXHAUSTED, DoneReason.BUDGET_EXHAUSTED
                    )
                before_dirty = set(self._git_changed_files())
                task_key = self._task_key_for_opportunity(op)
                attempts = self._lineage_attempts.get(task_key, 0) + 1
                task = AutonomousTask(
                    task_id=f"wave-{wave}-{index}",
                    title=op.title,
                    rationale=op.evidence,
                    priority=op.priority,
                    confidence=op.confidence,
                    verification_plan=(
                        ["pytest -q tests/test_runner_defaults.py"]
                        if (self.repo / "tests").exists()
                        else []
                    ),
                    task_contract=op.task_contract,
                    task_key=task_key,
                    parent_task_key=self._parent_task_key(op),
                    origin_kind=op.category,
                    attempts=attempts,
                    retries=max(0, attempts - 1),
                )
                self._lineage_attempts[task_key] = attempts
                self._attempt_counter += 1
                self._execute_task(task)
                self._lineage_last_fingerprint[task_key] = self._repo_fingerprint_for_task(task_key)

                after_dirty = set(self._git_changed_files())
                task.files_changed = sorted(after_dirty - before_dirty)
                delta_basis = (
                    task.files_changed
                    or task.intentional_changes
                    or task.incidental_changes
                )
                task.intentional_changes, task.incidental_changes, _ = (
                    self._split_changes(delta_basis)
                )
                if not task.files_changed:
                    task.files_changed = sorted(
                        set(task.intentional_changes) | set(task.incidental_changes)
                    )
                task.produced_effect = bool(task.intentional_changes)
                self._lineage_last_intentional_changes[task_key] = tuple(sorted(task.intentional_changes))
                wave_files.update(task.intentional_changes)

                verification = self.verifier.verify(
                    op.proposed_next_action,
                    task.intentional_changes,
                    task.verification_results,
                    validation_artifacts=task.validation_artifacts,
                    intended_targets=task.intended_targets,
                    before_contents=task.before_contents,
                )
                task.verification_results.append(
                    {
                        "summary": verification.summary,
                        "status": verification.status.value,
                        "confidence": verification.confidence_score,
                        "findings": [
                            f"{f.category.value}: {f.message}"
                            for f in verification.findings
                        ],
                    }
                )
                task.status, task.outcome = self._adjudicate_task(task, verification)
                task.status = self._update_lifecycle_after_attempt(task, op)
                self._update_category_attempt_state(task)
                if task.status == TaskLifecycle.PASSED.value:
                    retired += 1
                elif task.status == TaskLifecycle.RETRYABLE.value:
                    retryable += 1
                elif task.status == TaskLifecycle.BLOCKED.value:
                    blocked += 1
                self.attempted.append(task)

            if self.takeover_config.max_files_per_wave > 0 and len(wave_files) > self.takeover_config.max_files_per_wave:
                state.current_risk_level = "high"

            if wave_files and not self._wave_has_validation_artifact(
                self.attempted[-len(selected) :]
            ):
                self._followup_queue.append(
                    self._validate_recent_changes_followup(sorted(wave_files))
                )

            avg_conf = round(
                sum(t.confidence for t in self.attempted[-len(selected) :])
                / max(1, len(selected)),
                2,
            )
            state.completed_waves.append(
                {
                    "wave": wave,
                    "retired": retired,
                    "retryable": retryable,
                    "blocked": blocked,
                    "confidence": avg_conf,
                    "files_touched": sorted(wave_files),
                }
            )
            self._emit(
                "takeover_wave_complete",
                wave=wave,
                retired=retired,
                confidence=avg_conf,
                risk=state.current_risk_level,
            )

            current_changes = set(self._git_changed_files())
            repo_delta = current_changes != prior_changed_snapshot or bool(wave_files)
            model_activity = self._model_request_count > self._last_model_request_count
            if model_activity:
                self._planner_only_cycles = 0
            else:
                self._planner_only_cycles += 1
                self._emit(
                    "villani_planner_churn",
                    wave=wave,
                    planner_only_cycles=self._planner_only_cycles,
                    model_request_count=self._model_request_count,
                    repo_delta=repo_delta,
                )
            self._last_model_request_count = self._model_request_count

            progress_made = repo_delta or retired > 0
            if progress_made:
                stagnation_cycles = 0
            else:
                stagnation_cycles += 1
                self._emit(
                    "takeover_stagnation",
                    wave=wave,
                    count=stagnation_cycles,
                    limit=self.takeover_config.stagnation_cycle_limit,
                )
            prior_changed_snapshot = current_changes

            if self._planner_only_cycles >= 2 or (self._planner_only_cycles >= 2 and not repo_delta):
                reason = DoneReason.PLANNER_CHURN
                self._stop_rationale["planner_churn"] = reason
                return self._finalize_stop(state, StopDecision.PLANNER_CHURN, reason)

            if (
                self.takeover_config.stagnation_cycle_limit > 0
                and stagnation_cycles >= self.takeover_config.stagnation_cycle_limit
            ):
                return self._finalize_stop(
                    state,
                    StopDecision.STAGNATION,
                    f"No meaningful progress after {stagnation_cycles} consecutive cycles.",
                )


    def inspect_repo(self) -> RepoSnapshot:
        files = sorted(
            p.relative_to(self.repo).as_posix()
            for p in self.repo.rglob("*")
            if p.is_file()
            and not is_ignored_repo_path(p.relative_to(self.repo).as_posix())
        )
        key = [
            f
            for f in files
            if f in {"README.md", "pyproject.toml", "getting-started.md"}
            or f.startswith("villani_code/")
        ][:40]
        docs = [f for f in files if f.startswith("docs/") or f.endswith(".md")][:40]
        tests = [f for f in files if f.startswith("tests/")][:80]
        config = [f for f in files if f.endswith((".toml", ".yaml", ".yml", ".json"))][
            :60
        ]
        ci = [
            f for f in files if ".github/workflows/" in f or f.startswith(".github/")
        ][:20]
        todos = self._todo_hits(files)
        cmds = self._detect_tooling_commands(files)
        self._emit(
            "autonomous_scan",
            files_inspected=len(files),
            key_files=key[:10],
            tooling_commands=cmds,
        )
        return RepoSnapshot(
            key_files=key,
            docs=docs,
            tests=tests,
            config_files=config,
            ci_files=ci,
            tooling_commands=cmds,
            todo_hits=todos,
        )

    def generate_candidates(self, snapshot: RepoSnapshot) -> list[AutonomousTask]:
        candidates: list[AutonomousTask] = []
        for op in self.planner.discover_opportunities():
            candidates.append(
                AutonomousTask(
                    task_id=op.title.lower().replace(" ", "-"),
                    title=op.title,
                    rationale=op.evidence,
                    priority=op.priority,
                    confidence=op.confidence,
                    verification_plan=[op.proposed_next_action],
                    task_contract=op.task_contract,
                )
            )
        self._emit(
            "autonomous_candidates",
            count=len(candidates),
            tasks=[c.title for c in candidates],
        )
        return candidates

    def rank_tasks(self, tasks: list[AutonomousTask]) -> list[AutonomousTask]:
        ranked = sorted(
            tasks, key=lambda t: (t.priority * 0.7 + t.confidence * 0.3), reverse=True
        )
        self._emit(
            "autonomous_phase", phase="ranking tasks", ranked=[t.title for t in ranked]
        )
        return ranked

    def _build_wave_candidates(
        self, discovered: list[Opportunity]
    ) -> list[Opportunity]:
        from villani_code import autonomous_helpers

        candidates = autonomous_helpers.build_wave_candidates(self, discovered)
        self._retryable_queue = []
        self._followup_queue = []
        return candidates

    def _effective_priority(self, op: Opportunity) -> float:
        from villani_code import autonomous_helpers

        return autonomous_helpers.effective_priority(op)

    def _task_key_for_opportunity(self, op: Opportunity) -> str:
        from villani_code import autonomous_helpers

        return autonomous_helpers.task_key_for_opportunity(op)

    def _parent_task_key(self, op: Opportunity) -> str:
        from villani_code import autonomous_helpers

        return autonomous_helpers.parent_task_key(op)

    def _retry_limit_for_contract(self, contract: str) -> int:
        from villani_code import autonomous_helpers

        return autonomous_helpers.retry_limit_for_contract(contract)

    def _is_terminal_lineage(self, task_key: str) -> bool:
        from villani_code import autonomous_helpers

        return autonomous_helpers.is_terminal_lineage(self, task_key)

    def _is_actionable_failure(self, task: AutonomousTask) -> bool:
        from villani_code import autonomous_helpers

        return autonomous_helpers.is_actionable_failure(task)

    def _generate_followups(
        self, task: AutonomousTask, op: Opportunity
    ) -> list[Opportunity]:
        followups: list[Opportunity] = []
        if task.status == TaskLifecycle.BLOCKED.value:
            if task.runner_failures:
                followups.append(
                    Opportunity(
                        title=f"Unblock {op.title}",
                        category="followup_repair",
                        priority=0.88,
                        confidence=0.68,
                        affected_files=task.intentional_changes or op.affected_files,
                        evidence="Concrete blocker found; attempt narrow unblock.",
                        blast_radius="small",
                        proposed_next_action="apply the smallest unblock needed for the prior task",
                        task_contract=TaskContract.EFFECTFUL.value,
                    )
                )
            return followups

        if (
            task.task_contract == TaskContract.EFFECTFUL.value
            and task.produced_effect
            and task.status != TaskLifecycle.PASSED.value
        ):
            followups.append(
                Opportunity(
                    title=f"Complete {op.title.lower()}",
                    category="followup_repair",
                    priority=0.9,
                    confidence=0.76,
                    affected_files=task.intentional_changes or op.affected_files,
                    evidence="Partial edits were made without full contract completion.",
                    blast_radius="small",
                    proposed_next_action="finish the narrow missing repair and verify changed files",
                    task_contract=TaskContract.EFFECTFUL.value,
                )
            )

        if (
            task.task_contract == TaskContract.VALIDATION.value
            and task.produced_effect
            and not task.produced_validation
        ):
            followups.append(
                Opportunity(
                    title=f"Re-run {op.title.lower()} validation",
                    category="followup_validation",
                    priority=0.94,
                    confidence=0.82,
                    affected_files=task.intentional_changes or op.affected_files,
                    evidence="Validation task edited files but produced no validation evidence.",
                    blast_radius="small",
                    proposed_next_action="run focused validation only on recently changed files",
                    task_contract=TaskContract.VALIDATION.value,
                )
            )

        return followups

    def _insert_followup(self, followup: Opportunity, source_task_id: str) -> None:
        task_key = self._task_key_for_opportunity(followup)
        current_fingerprint = self._repo_fingerprint_for_task(task_key)
        skip_reason = ""
        if self._followup_already_pending(followup.title):
            skip_reason = "duplicate_pending_title"
        elif self._is_task_satisfied(task_key):
            skip_reason = "already_satisfied_for_fingerprint"
        elif self._is_terminal_lineage(task_key) and self._lineage_terminal_fingerprint.get(task_key) == current_fingerprint:
            skip_reason = "terminal_lineage_unchanged"

        if skip_reason:
            self._followup_skip_reasons.append({"title": followup.title, "reason": skip_reason})
            self._emit("villani_followup_skipped", title=followup.title, reason=skip_reason)
            return

        self._emit(
            "villani_followup_inserted",
            title=followup.title,
            category=followup.category,
            source_task_id=source_task_id,
        )
        self._followup_queue.append(followup)
        self._backlog_insertions.append(
            {
                "title": followup.title,
                "category": followup.category,
                "rationale": followup.evidence,
                "evidence": followup.evidence,
                "confidence": followup.confidence,
                "estimated_risk": followup.blast_radius,
                "source_task_id": source_task_id,
            }
        )
        self._log(f"[villani-mode] planner inserted follow-up: {followup.title}")

    def _update_lifecycle_after_attempt(
        self, task: AutonomousTask, op: Opportunity
    ) -> str:
        task_key = task.task_key
        retries_used = self._lineage_retries.get(task_key, 0)
        retry_limit = self._retry_limit_for_contract(task.task_contract)

        if task.status == TaskLifecycle.PASSED.value:
            self._mark_task_satisfied(task)
            for followup in self._deterministic_followups(task, op):
                self._insert_followup(followup, task.task_id)
            self._lineage_status[task_key] = TaskLifecycle.PASSED.value
            self._lineage_terminal_fingerprint[task_key] = self._repo_fingerprint_for_task(task_key)
            return TaskLifecycle.PASSED.value

        if task.status == TaskLifecycle.BLOCKED.value or any(
            "permission" in f.lower() or "denied" in f.lower()
            for f in task.runner_failures
        ):
            self._lineage_status[task_key] = TaskLifecycle.BLOCKED.value
            self._lineage_blockers[task_key] = "; ".join(task.runner_failures[:2])
            for followup in self._generate_followups(task, op):
                self._insert_followup(followup, task.task_id)
            self._lineage_terminal_fingerprint[task_key] = self._repo_fingerprint_for_task(task_key)
            return TaskLifecycle.BLOCKED.value

        actionable = self._is_actionable_failure(task)
        if actionable:
            for followup in self._generate_followups(task, op):
                self._insert_followup(followup, task.task_id)

        if retries_used < retry_limit:
            if self._is_stale_repeat(task):
                stale_marker = f"stale_repeat:{task_key}"
                self._stale_task_keys.add(task_key)
                self._critic_outcomes.append(stale_marker)
                self._lineage_status[task_key] = TaskLifecycle.EXHAUSTED.value
                self._lineage_terminal_fingerprint[task_key] = self._repo_fingerprint_for_task(task_key)
                if task.task_contract in {TaskContract.VALIDATION.value, TaskContract.INSPECTION.value}:
                    return TaskLifecycle.EXHAUSTED.value
                return TaskLifecycle.EXHAUSTED.value
            self._lineage_retries[task_key] = retries_used + 1
            retry_confidence = max(op.confidence, 0.62)
            self._retryable_queue.append(
                Opportunity(
                    title=op.title,
                    category="retryable",
                    priority=min(0.95, op.priority + 0.03),
                    confidence=retry_confidence,
                    affected_files=op.affected_files,
                    evidence=f"retry attempt {retries_used + 1} for lineage {task_key}",
                    blast_radius=op.blast_radius,
                    proposed_next_action=op.proposed_next_action,
                    task_contract=op.task_contract,
                )
            )
            self._lineage_status[task_key] = TaskLifecycle.RETRYABLE.value
            return TaskLifecycle.RETRYABLE.value

        self._lineage_status[task_key] = TaskLifecycle.EXHAUSTED.value
        self._lineage_terminal_fingerprint[task_key] = self._repo_fingerprint_for_task(task_key)
        return TaskLifecycle.EXHAUSTED.value

    def _validate_recent_changes_followup(
        self, changed_files: list[str]
    ) -> Opportunity:
        return Opportunity(
            title="Validate recent autonomous changes",
            category="followup_validation",
            priority=0.96,
            confidence=0.82,
            affected_files=changed_files[:5],
            evidence="Authoritative files changed without successful validation artifact.",
            blast_radius="small",
            proposed_next_action="run focused validation path for recently changed files",
            task_contract=TaskContract.VALIDATION.value,
        )

    def _wave_has_validation_artifact(self, tasks: list[AutonomousTask]) -> bool:
        return any(
            t.task_contract == TaskContract.VALIDATION.value and t.produced_validation
            for t in tasks
        )

    def _has_pending_actionable_work(self) -> bool:
        from villani_code import autonomous_helpers

        return autonomous_helpers.has_pending_actionable_work(self)

    def _execute_task(self, task: AutonomousTask) -> None:
        task.status = TaskLifecycle.RUNNING.value
        self._emit("autonomous_phase", phase=f"Villani mode task started: {task.title}")
        objective = (
            "You are in Villani mode. Execute the selected intervention end-to-end, make meaningful repository improvements, and summarize exact edits and validation. "
            f"Intervention: {task.title}\nEvidence: {task.rationale}"
        )
        if task.title == "Inspect repo for highest-leverage improvement":
            objective += (
                "\nFollow this inspection plan in order where files exist: "
                "1) top-level README.md or README.rst, 2) pyproject.toml, "
                "3) package root directory or src/ layout, 4) up to 3 representative Python source files, "
                "5) existing test files if any. Then produce exactly one of: "
                "small safe code improvement, small safe docs improvement, minimal test bootstrap, "
                "or conclude no clear improvement is justified."
            )
        if task.title == "Validate baseline importability":
            family = shell_family_for_platform(sys.platform)
            cmd = baseline_import_validation_command(family)
            objective += (
                "\nValidation contract (mandatory): inspect relevant package layout first, then run one import validation command "
                f"(prefer {cmd}), capture the executed command output and exit code, and only then summarize result. "
                "No network, no installs, and no broad recursive execution."
            )
        self._emit(
            "villani_model_request_started",
            task_id=task.task_id,
            title=task.title,
            attempt=task.attempts,
            task_contract=task.task_contract,
        )
        self._model_request_count += 1
        self._wave_had_model_activity = True
        result = self.runner.run(objective, execution_budget=None)
        task.outcome = "\n".join(
            block.get("text", "")
            for block in result.get("response", {}).get("content", [])
            if block.get("type") == "text"
        )
        execution = result.get("execution", {})
        task.terminated_reason = str(execution.get("terminated_reason", "error"))
        task.turns_used = int(execution.get("turns_used", 0))
        task.tool_calls_used = int(execution.get("tool_calls_used", 0))
        task.elapsed_seconds = float(execution.get("elapsed_seconds", 0.0))
        self._emit(
            "villani_model_request_finished",
            task_id=task.task_id,
            title=task.title,
            attempt=task.attempts,
            terminated_reason=task.terminated_reason,
            turns_used=task.turns_used,
            tool_calls_used=task.tool_calls_used,
            elapsed_seconds=task.elapsed_seconds,
        )
        task.files_changed = list(
            execution.get("all_changes", execution.get("files_changed", []))
        )
        task.intentional_changes = list(execution.get("intentional_changes", []))
        task.incidental_changes = list(execution.get("incidental_changes", []))
        task.intended_targets = list(execution.get("intended_targets", []))
        task.before_contents = dict(execution.get("before_contents", {}))
        task.verification_results = self._extract_commands(result)
        task.validation_artifacts = list(execution.get("validation_artifacts", []))
        task.inspection_summary = str(execution.get("inspection_summary", "")).strip()
        task.runner_failures = list(
            execution.get("runner_failures", [])
        ) or self._extract_runner_failures(result)
        if classify_shell_portability_failure(r.get("command", "") for r in task.verification_results):
            self._critic_outcomes.append("shell_portability_failure")
        task.produced_effect = bool(task.intentional_changes)
        task.produced_validation = self._has_hard_validation_evidence(task)
        task.produced_inspection_conclusion = bool(task.inspection_summary)
        task.completed = task.terminated_reason == "completed"
        task.status = (
            TaskLifecycle.RUNNING.value
            if task.completed
            else TaskLifecycle.FAILED.value
        )
        self._emit(
            "autonomous_phase",
            phase=f"Villani mode task stopped: {task.terminated_reason}",
        )
        self._emit(
            "autonomous_phase",
            phase=(
                f"Turns: {task.turns_used}, tool calls: {task.tool_calls_used}, "
                f"elapsed: {task.elapsed_seconds:.2f}s, files changed: {len(task.files_changed)}"
            ),
        )
        if task.intentional_changes:
            self._emit(
                "autonomous_phase",
                phase=f"Intentional files changed: {', '.join(task.intentional_changes)}",
            )
        elif task.incidental_changes:
            self._emit(
                "autonomous_phase",
                phase=f"Incidental files changed: {', '.join(task.incidental_changes)}",
            )
        if self._transcript_contains_denied(result):
            task.status = TaskLifecycle.BLOCKED.value
            task.outcome += "\nBlocked by hard safety policy."
        if not self._has_any_evidence(task):
            task.outcome = (
                task.outcome + "\n" if task.outcome else ""
            ) + "No intervention or validation evidence produced."
            if task.status in {TaskLifecycle.RUNNING.value, TaskLifecycle.FAILED.value}:
                task.status = TaskLifecycle.FAILED.value

    def _adjudicate_task(
        self, task: AutonomousTask, verification: Any
    ) -> tuple[str, str]:
        if task.status == TaskLifecycle.BLOCKED.value:
            return TaskLifecycle.BLOCKED.value, "blocked_by_policy_or_environment"

        if task.terminated_reason in {
            "no_edits",
            "recon_loop",
            "model_idle",
        } and not self._has_any_evidence(task):
            return (
                TaskLifecycle.FAILED.value,
                "no_effect: No intervention or validation evidence produced.",
            )

        if (
            task.task_contract == TaskContract.VALIDATION.value
            and not self._has_hard_validation_evidence(task)
        ):
            return (
                TaskLifecycle.FAILED.value,
                "validation_not_executed: no concrete validation command artifact was produced.",
            )

        if verification.status == VerificationStatus.UNCERTAIN:
            return (
                TaskLifecycle.FAILED.value,
                "verification_uncertain: task requires concrete evidence before pass.",
            )

        if not self._meets_contract(task):
            return (
                TaskLifecycle.FAILED.value,
                "contract_unsatisfied: no evidence produced for task contract.",
            )

        if task.runner_failures and not (
            task.produced_effect or task.produced_validation
        ):
            return (
                TaskLifecycle.FAILED.value,
                "runner_failures_unresolved: No intervention or validation evidence produced.",
            )

        blocking = {
            FailureCategory.TEST_FAILURE.value,
            FailureCategory.VERIFICATION_FAILURE.value,
            FailureCategory.TOOL_FAILURE.value,
        }
        if any(f.split(":", 1)[0] in blocking for f in task.runner_failures) and not (
            task.produced_effect or task.produced_validation
        ):
            return (
                TaskLifecycle.FAILED.value,
                "runner_failure_blocked_pass: No intervention or validation evidence produced.",
            )

        if verification.status == VerificationStatus.PASS:
            return TaskLifecycle.PASSED.value, "passed"

        return TaskLifecycle.FAILED.value, "verification_failed"

    @staticmethod
    def _has_any_evidence(task: AutonomousTask) -> bool:
        from villani_code import autonomous_helpers

        return autonomous_helpers.has_any_evidence(task)

    def _meets_contract(self, task: AutonomousTask) -> bool:
        from villani_code import autonomous_helpers

        return autonomous_helpers.meets_contract(task)

    def _meets_effectful_minimum(self, task: AutonomousTask) -> bool:
        from villani_code import autonomous_helpers

        return autonomous_helpers.meets_effectful_minimum(task)

    def _meets_validation_minimum(self, task: AutonomousTask) -> bool:
        from villani_code import autonomous_helpers

        return autonomous_helpers.meets_validation_minimum(task)

    @staticmethod
    def _has_real_validation_artifact(task: AutonomousTask) -> bool:
        from villani_code import autonomous_helpers

        return autonomous_helpers.has_real_validation_artifact(task)

    @staticmethod
    def _has_hard_validation_evidence(task: AutonomousTask) -> bool:
        if VillaniModeController._has_real_validation_artifact(task):
            return True
        for result in task.verification_results:
            if not isinstance(result, dict):
                continue
            command = str(result.get("command", "")).strip()
            if command and int(result.get("exit", 1)) == 0:
                return True
        for artifact in task.validation_artifacts:
            text = str(artifact).lower()
            if "(exit=0)" in text and any(tok in text for tok in ["python", "pytest", "uv", "tox", "nox", "make", "poetry", "pip"]):
                return True
        return False

    @staticmethod
    def _is_test_file(path: str) -> bool:
        norm = path.replace("\\", "/").lstrip("./")
        name = Path(norm).name
        return (
            norm.startswith("tests/")
            and norm.endswith(".py")
            or (name.startswith("test_") and name.endswith(".py"))
        )

    def _extract_runner_failures(self, result: dict[str, Any]) -> list[str]:
        from villani_code import autonomous_reporting

        return autonomous_reporting.extract_runner_failures(result)

    def _extract_commands(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        from villani_code import autonomous_reporting

        return autonomous_reporting.extract_commands(result)

    def _finalize_stop(
        self, state: TakeoverState, decision_kind: StopDecision, done_reason: str
    ) -> dict[str, Any]:
        self._stop_decision_kind = decision_kind
        self._emit(
            "villani_stop_decision",
            stop_decision_kind=decision_kind,
            done_reason=done_reason,
            model_request_count=self._model_request_count,
            planner_only_cycles=self._planner_only_cycles,
        )
        return self._build_takeover_summary(state, done_reason)

    def _build_takeover_summary(
        self, state: TakeoverState, done_reason: str
    ) -> dict[str, Any]:
        from villani_code import autonomous_reporting

        return autonomous_reporting.build_takeover_summary(
            state=state,
            attempted=self.attempted,
            current_changes=set(self._git_changed_files()),
            preexisting_changes=self._preexisting_changes,
            done_reason=done_reason,
            recommended_next_steps_value=self._recommended_next_steps(),
            blocked_value=TaskLifecycle.BLOCKED.value,
            opportunities_considered=self._opportunities_considered,
            opportunities_attempted=self._opportunities_attempted,
            working_memory={
                "satisfied_task_keys": self._satisfied_task_keys,
                "invalidated_task_keys": sorted(self._invalidated_task_keys),
                "backlog_insertions": self._backlog_insertions,
                "filtered_opportunity_reasons": self._filtered_reasons_by_category,
                "category_examination_state": self._category_state,
                "stop_decision_rationale": self._stop_rationale,
                "critic_outcomes": self._critic_outcomes,
                "model_request_count": self._model_request_count,
                "planner_only_cycles": self._planner_only_cycles,
                "followup_skip_reasons": self._followup_skip_reasons,
                "stop_decision_kind": self._stop_decision_kind.value if self._stop_decision_kind else "",
            },
        )


    def _detect_tooling_commands(self, files: list[str]) -> list[str]:
        from villani_code import autonomous_reporting

        return autonomous_reporting.detect_tooling_commands(files)

    def _todo_hits(self, files: list[str]) -> list[str]:
        from villani_code import autonomous_reporting

        return autonomous_reporting.todo_hits(self.repo, files)

    def _recommended_next_steps(self) -> list[str]:
        from villani_code import autonomous_reporting

        return autonomous_reporting.recommended_next_steps(
            self.attempted,
            TaskLifecycle.BLOCKED.value,
            {
                TaskLifecycle.FAILED.value,
                TaskLifecycle.RETRYABLE.value,
                TaskLifecycle.EXHAUSTED.value,
            },
        )

    def _repo_fingerprint_for_task(self, task_key: str) -> str:
        relevant: list[str] = []
        if "importability" in task_key or "import" in task_key:
            for p in self.repo.rglob("*.py"):
                if not p.is_file():
                    continue
                rel = p.relative_to(self.repo).as_posix()
                if rel.startswith("tests/"):
                    continue
                digest = hashlib.sha1(p.read_bytes()).hexdigest()
                relevant.append(f"{rel}:{digest}")
        else:
            relevant = [f"dirty:{f}" for f in self._git_changed_files()]
        return "|".join(sorted(relevant)[:200])

    def _mark_task_satisfied(self, task: AutonomousTask) -> None:
        key = task.task_key
        self._satisfied_task_keys[key] = self._repo_fingerprint_for_task(key)
        self._log(f"[villani-mode] critic marked {task.title.lower()} as session-satisfied")

    def _is_task_satisfied(self, task_key: str) -> bool:
        fingerprint = self._satisfied_task_keys.get(task_key)
        if not fingerprint:
            return False
        current = self._repo_fingerprint_for_task(task_key)
        if current != fingerprint:
            self._invalidated_task_keys.add(task_key)
            return False
        return True

    def _is_stale_repeat(self, task: AutonomousTask) -> bool:
        if task.attempts <= 1:
            return False
        prior_fingerprint = self._lineage_last_fingerprint.get(task.task_key)
        current_fingerprint = self._repo_fingerprint_for_task(task.task_key)
        same_fingerprint = prior_fingerprint == current_fingerprint
        prior_changes = self._lineage_last_intentional_changes.get(task.task_key, tuple())
        same_changes = tuple(sorted(task.intentional_changes)) == prior_changes
        no_task_delta = not task.intentional_changes and not task.files_changed
        no_model_wave_activity = not self._wave_had_model_activity
        return all([
            same_fingerprint,
            same_changes,
            no_task_delta,
            no_model_wave_activity,
            not task.validation_artifacts,
        ])

    def _deterministic_followups(self, task: AutonomousTask, op: Opportunity) -> list[Opportunity]:
        followups: list[Opportunity] = []
        if task.title != "Validate baseline importability" or task.status != TaskLifecycle.PASSED.value:
            return followups

        if self._category_state.get("tests") == "discovered" and self._repo_has_test_files():
            followups.append(
                Opportunity(
                    "Run baseline tests",
                    "followup_tests",
                    0.98,
                    0.9,
                    ["tests/"],
                    "baseline import validation succeeded and tests are present",
                    "small",
                    "run baseline tests",
                    TaskContract.VALIDATION.value,
                )
            )

        entrypoint_key = self._task_key_for_opportunity(
            Opportunity("Validate CLI entrypoint", "followup_entrypoint", 0.94, 0.84, op.affected_files, "", "small", "validate CLI entrypoint", TaskContract.VALIDATION.value)
        )
        if (
            self._repo_has_cli_entrypoint()
            and self._category_state.get("entrypoints") == "discovered"
            and not self._is_task_satisfied(entrypoint_key)
            and not self._followup_already_pending("Validate CLI entrypoint")
            and (not self._task_attempted_this_session(entrypoint_key) or self._lineage_last_fingerprint.get(entrypoint_key) != self._repo_fingerprint_for_task(entrypoint_key))
        ):
            followups.append(
                Opportunity("Validate CLI entrypoint", "followup_entrypoint", 0.94, 0.84, op.affected_files, "entrypoints discovered during exploration", "small", "validate CLI entrypoint", TaskContract.VALIDATION.value)
            )

        if self._category_state.get("docs") == "discovered" and self._repo_has_docs_examples():
            followups.append(
                Opportunity("Validate documented commands/examples", "followup_docs", 0.93, 0.82, ["README.md"], "docs present and may include runnable examples", "small", "validate documented commands/examples", TaskContract.INSPECTION.value)
            )
        return followups

    def _repo_has_cli_entrypoint(self) -> bool:
        pyproject = self.repo / "pyproject.toml"
        if pyproject.exists():
            text = pyproject.read_text(encoding="utf-8", errors="replace")
            if "[project.scripts]" in text or "[tool.poetry.scripts]" in text or "[project.entry-points" in text:
                return True
        for candidate in ("villani_code/cli.py", "cli.py"):
            if (self.repo / candidate).is_file():
                return True
        return False

    def _repo_has_docs_examples(self) -> bool:
        if (self.repo / "README.md").is_file() or (self.repo / "getting-started.md").is_file():
            return True
        return (self.repo / "docs").is_dir()

    def _repo_has_test_files(self) -> bool:
        tests = self.repo / "tests"
        if not tests.is_dir():
            return False
        return any(self._is_test_file(p.relative_to(self.repo).as_posix()) for p in tests.rglob("*.py") if p.is_file())

    def _followup_already_pending(self, title: str) -> bool:
        return any(op.title == title for op in self._followup_queue)

    def _task_attempted_this_session(self, task_key: str) -> bool:
        return self._lineage_attempts.get(task_key, 0) > 0

    def _mark_category_discovery(self) -> None:
        from villani_code import autonomous_progress

        autonomous_progress.mark_category_discovery(
            self.repo, self._category_state, self._is_test_file
        )

    def _update_category_attempt_state(self, task: AutonomousTask) -> None:
        from villani_code import autonomous_progress

        autonomous_progress.update_category_attempt_state(
            self._category_state, task.title
        )

    def _enqueue_surface_followups_if_needed(self) -> bool:
        from villani_code import autonomous_progress

        inserted = False
        for followup in autonomous_progress.surface_followups(self._category_state):
            if followup.title == "Run baseline tests" and not self._repo_has_test_files():
                self._emit("villani_followup_skipped", title=followup.title, reason="no_test_files")
                continue
            if followup.title == "Validate documented commands/examples" and not self._repo_has_docs_examples():
                self._emit("villani_followup_skipped", title=followup.title, reason="no_docs_examples")
                continue
            if followup.title == "Validate CLI entrypoint" and not self._repo_has_cli_entrypoint():
                self._emit("villani_followup_skipped", title=followup.title, reason="no_cli_entrypoint")
                continue
            before = len(self._followup_queue)
            self._insert_followup(followup, "system")
            inserted = inserted or len(self._followup_queue) > before
            if followup.title == "Run baseline tests":
                self._log("[villani-mode] stop blocked: tests remain unexamined")
        return inserted

    def _stop_reason_from_categories(self) -> str:
        from villani_code import autonomous_progress

        self._stop_rationale, reason = autonomous_progress.stop_reason_from_categories(
            self._category_state
        )
        return reason

    def _git_changed_files(self) -> list[str]:
        proc = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        changed: list[str] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            changed.append(line[3:].strip())
        return changed

    def _split_changes(
        self, files: list[str]
    ) -> tuple[list[str], list[str], list[str]]:
        intentional: list[str] = []
        incidental: list[str] = []
        for path in files:
            if (
                is_ignored_repo_path(path)
                or classify_repo_path(path) != "authoritative"
            ):
                incidental.append(path)
            else:
                intentional.append(path)
        all_changes = sorted(set(intentional) | set(incidental))
        return sorted(set(intentional)), sorted(set(incidental)), all_changes

    def _transcript_contains_denied(self, result: dict[str, Any]) -> bool:
        transcript = result.get("transcript", {})
        for tool_result in transcript.get("tool_results", []):
            content = str(tool_result.get("content", ""))
            if (
                "Denied by permission policy" in content
                or "Refusing command" in content
            ):
                return True
        return False

    def _log(self, message: str) -> None:
        self._emit("autonomous_phase", phase=message)

    def _emit(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type}
        event.update(payload)
        self.event_callback(event)

    @staticmethod
    def format_summary(summary: dict[str, Any]) -> str:
        lines = ["# Villani mode summary", ""]
        lines.append(f"Repo assessment: {summary.get('repo_summary', '')}")
        lines.append("## Tasks")
        for task in summary.get("tasks_attempted", []):
            lines.append(
                f"- {task['title']} :: {task['status']} ({task.get('task_contract', 'inspection')})"
            )
            if task.get("attempts", 0) > 0:
                lines.append(
                    f"  - attempts: {task.get('attempts')} retries: {task.get('retries', 0)}"
                )
            if task.get("intentional_changes"):
                lines.append(
                    f"  - intentional_changed: {json.dumps(task.get('intentional_changes', []))}"
                )
            elif task.get("incidental_changes"):
                lines.append("  - changed: []")
                lines.append(
                    f"  - incidental_changed: {json.dumps(task.get('incidental_changes', []))}"
                )
            if task.get("validation_artifacts"):
                lines.append(
                    f"  - validation_artifacts: {json.dumps(task.get('validation_artifacts', []))}"
                )
            if task.get("inspection_summary"):
                lines.append(
                    f"  - inspection_summary: {task.get('inspection_summary')}"
                )
            if task.get("runner_failures"):
                lines.append(
                    f"  - runner_failures: {json.dumps(task.get('runner_failures', []))}"
                )
            if task.get("reason") and task.get("status") != "passed":
                lines.append(f"  - reason: {task.get('reason')[:180]}")
            if (
                task.get("task_contract") == TaskContract.VALIDATION.value
                and not task.get("validation_artifacts")
            ):
                lines.append(
                    "  - validation_not_executed: no concrete validation command artifact was produced."
                )
            for vr in task.get("verification", []):
                lines.append(f"  - verification: {json.dumps(vr)}")
        lines.append("")
        waves = summary.get("completed_waves", [])
        for wave in waves:
            lines.append(
                f"wave {wave.get('wave')}: retired={wave.get('retired')} retryable={wave.get('retryable', 0)} blocked={wave.get('blocked', 0)} files={len(wave.get('files_touched', []))}"
            )
        lines.append(f"Done reason: {summary.get('done_reason', '')}")
        lines.append(f"Blockers: {json.dumps(summary.get('blockers', []))}")
        lines.append(
            f"Preexisting changes: {json.dumps(summary.get('preexisting_changes', []))}"
        )
        lines.append(f"Files changed: {json.dumps(summary.get('files_changed', []))}")
        lines.append(
            f"Intentional changes: {json.dumps(summary.get('intentional_changes', []))}"
        )
        incidental = summary.get("incidental_changes", [])
        if incidental:
            lines.append(f"Incidental changes: {json.dumps(incidental)}")
        for step in summary.get("recommended_next_steps", []):
            lines.append(f"Next: {step}")

        memory = summary.get("working_memory", {})
        done_reason = str(summary.get("done_reason", "")).lower()
        if "budget exhausted" in done_reason:
            termination_kind = "budget exhausted"
        elif "no meaningful progress" in done_reason:
            termination_kind = "stagnation"
        elif "planner loop with no model activity" in done_reason:
            termination_kind = "planner loop with no model activity"
        elif "no opportunities" in done_reason:
            termination_kind = "no opportunities"
        else:
            termination_kind = memory.get("stop_decision_kind", "unknown")
        lines.append("")
        lines.append("## Villani control loop")
        lines.append(f"- model_requests: {memory.get('model_request_count', 0)}")
        lines.append(f"- backlog_insertions: {len(memory.get('backlog_insertions', []))}")
        lines.append(f"- stop_reason: {summary.get('done_reason', '')}")
        lines.append(f"- critic_outcomes: {json.dumps(memory.get('critic_outcomes', []))}")
        lines.append(f"- termination_kind: {termination_kind}")
        return "\n".join(lines)
