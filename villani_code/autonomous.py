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
from villani_code.evidence import parse_command_evidence
from villani_code.execution import VILLANI_TASK_BUDGET
from villani_code.shells import (
    baseline_import_validation_command,
    classify_shell_portability_failure,
    shell_family_for_platform,
)
from villani_code.repo_rules import (
    classify_repo_path,
    is_authoritative_doc_path,
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

    def run(self) -> dict[str, Any]:
        self._preexisting_changes = set(self._git_changed_files())
        state = TakeoverState(repo_summary=self.planner.build_repo_summary())
        self._emit(
            "takeover_dashboard",
            summary=state.repo_summary,
            wave=0,
            risk=state.current_risk_level,
        )

        for wave in range(1, self.takeover_config.max_waves + 1):
            if self._attempt_counter >= self.takeover_config.max_total_task_attempts:
                return self._build_takeover_summary(state, "Villani mode budget exhausted.")

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
                if self._enqueue_surface_followups_if_needed():
                    continue
                if (
                    not discovered
                    and not self._retryable_queue
                    and not self._followup_queue
                ):
                    return self._build_takeover_summary(
                        state, "No opportunities discovered."
                    )
                if self._has_pending_actionable_work():
                    return self._build_takeover_summary(
                        state, "Retry budget exhausted for remaining work."
                    )
                return self._build_takeover_summary(state, self._stop_reason_from_categories())

            selected = candidates[: self.takeover_config.max_commands_per_wave]
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
                    self._attempt_counter
                    >= self.takeover_config.max_total_task_attempts
                ):
                    return self._build_takeover_summary(
                        state, "Villani mode budget exhausted."
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

            if len(wave_files) > self.takeover_config.max_files_per_wave:
                state.current_risk_level = "high"
                return self._build_takeover_summary(
                    state, "Blast radius exceeded configured max files per wave."
                )

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

        if self._has_pending_actionable_work():
            return self._build_takeover_summary(state, "Villani mode budget exhausted.")
        return self._build_takeover_summary(state, self._stop_reason_from_categories())

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
                    proposed_next_action="run bounded validation only on recently changed files",
                    task_contract=TaskContract.VALIDATION.value,
                )
            )

        return followups

    def _insert_followup(self, followup: Opportunity, source_task_id: str) -> None:
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
            return TaskLifecycle.PASSED.value

        if task.status == TaskLifecycle.BLOCKED.value or any(
            "permission" in f.lower() or "denied" in f.lower()
            for f in task.runner_failures
        ):
            self._lineage_status[task_key] = TaskLifecycle.BLOCKED.value
            self._lineage_blockers[task_key] = "; ".join(task.runner_failures[:2])
            for followup in self._generate_followups(task, op):
                self._insert_followup(followup, task.task_id)
            return TaskLifecycle.BLOCKED.value

        actionable = self._is_actionable_failure(task)
        if actionable:
            for followup in self._generate_followups(task, op):
                self._insert_followup(followup, task.task_id)

        if retries_used < retry_limit:
            if self._is_stale_repeat(task):
                self._stale_task_keys.add(task_key)
                self._critic_outcomes.append("stale_repetition")
                self._lineage_status[task_key] = TaskLifecycle.EXHAUSTED.value
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
            proposed_next_action="run bounded validation path for recently changed files",
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
            "You are in Villani mode. Execute one bounded intervention and summarize exact edits and validation. "
            f"Intervention: {task.title}\nEvidence: {task.rationale}"
        )
        if task.title == "Inspect repo for highest-leverage small improvement":
            objective += (
                "\nFollow this bounded inspection plan in order where files exist: "
                "1) top-level README.md or README.rst, 2) pyproject.toml, "
                "3) package root directory or src/ layout, 4) up to 3 representative Python source files, "
                "5) existing test files if any. Then produce exactly one of: "
                "small safe code improvement, small safe docs improvement, minimal test bootstrap, "
                "or conclude no clear bounded improvement is justified."
            )
        if task.title == "Validate baseline importability":
            family = shell_family_for_platform(sys.platform)
            cmd = baseline_import_validation_command(family)
            objective += (
                "\nValidation contract (mandatory): inspect relevant package layout first, then run one bounded import validation command "
                f"(prefer {cmd}), capture the executed command output and exit code, and only then summarize result. "
                "No network, no installs, and no broad recursive execution."
            )
        result = self.runner.run(objective, execution_budget=VILLANI_TASK_BUDGET)
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
        task.produced_validation = self._has_real_validation_artifact(task)
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
            and not self._has_real_validation_artifact(task)
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
    def _is_test_file(path: str) -> bool:
        norm = path.replace("\\", "/").lstrip("./")
        name = Path(norm).name
        return (
            norm.startswith("tests/")
            and norm.endswith(".py")
            or (name.startswith("test_") and name.endswith(".py"))
        )

    def _extract_runner_failures(self, result: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        for event in result.get("transcript", {}).get("events", []):
            if event.get("type") != "failure_classified":
                continue
            category = str(event.get("category", "tool_failure"))
            summary = str(event.get("summary", ""))
            failures.append(f"{category}: {summary}".strip())
        for tool_result in result.get("transcript", {}).get("tool_results", []):
            if tool_result.get("is_error"):
                failures.append(f"tool_failure: {tool_result.get('content', '')}"[:280])
        return failures

    def _extract_commands(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tr in result.get("transcript", {}).get("tool_results", []):
            for record in parse_command_evidence(str(tr.get("content", ""))):
                out.append(
                    {
                        "command": str(record.get("command", "")).strip(),
                        "exit": int(record.get("exit", 1)),
                    }
                )
        return out

    def _build_takeover_summary(
        self, state: TakeoverState, done_reason: str
    ) -> dict[str, Any]:
        current_changes = set(self._git_changed_files())
        preexisting = sorted(self._preexisting_changes)
        new_changes = sorted(current_changes - self._preexisting_changes)
        intentional_set = {p for t in self.attempted for p in t.intentional_changes}
        incidental_set = {p for t in self.attempted for p in t.incidental_changes}
        return {
            "repo_summary": state.repo_summary,
            "tasks_attempted": [
                {
                    "id": t.task_id,
                    "title": t.title,
                    "status": t.status,
                    "task_contract": t.task_contract,
                    "attempts": t.attempts,
                    "retries": t.retries,
                    "reason": t.outcome[:1200],
                    "verification": t.verification_results,
                    "validation_artifacts": t.validation_artifacts,
                    "inspection_summary": t.inspection_summary,
                    "runner_failures": t.runner_failures,
                    "produced_effect": t.produced_effect,
                    "produced_validation": t.produced_validation,
                    "produced_inspection_conclusion": t.produced_inspection_conclusion,
                    "files_changed": t.files_changed,
                    "intentional_changes": t.intentional_changes,
                    "incidental_changes": t.incidental_changes,
                    "terminated_reason": t.terminated_reason,
                    "turns_used": t.turns_used,
                    "tool_calls_used": t.tool_calls_used,
                    "elapsed_seconds": t.elapsed_seconds,
                    "completed": t.completed,
                }
                for t in self.attempted
            ],
            "files_changed": new_changes,
            "preexisting_changes": preexisting,
            "intentional_changes": sorted(intentional_set & set(new_changes)),
            "incidental_changes": sorted(incidental_set & set(new_changes)),
            "blockers": [
                t.title
                for t in self.attempted
                if t.status == TaskLifecycle.BLOCKED.value
            ],
            "done_reason": done_reason,
            "completed_waves": state.completed_waves,
            "recommended_next_steps": self._recommended_next_steps(),
            "working_memory": {
                "satisfied_task_keys": self._satisfied_task_keys,
                "invalidated_task_keys": sorted(self._invalidated_task_keys),
                "backlog_insertions": self._backlog_insertions,
                "filtered_opportunity_reasons": self._filtered_reasons_by_category,
                "category_examination_state": self._category_state,
                "stop_decision_rationale": self._stop_rationale,
                "critic_outcomes": self._critic_outcomes,
            },
        }

    def _detect_tooling_commands(self, files: list[str]) -> list[str]:
        commands: list[str] = []
        if any(f.startswith("tests/") for f in files):
            commands.append("pytest -q")
        return commands or ["git diff --stat"]

    def _todo_hits(self, files: list[str]) -> list[str]:
        hits: list[str] = []
        for rel in files:
            if len(hits) >= 20:
                break
            if is_ignored_repo_path(rel):
                continue
            if not rel.endswith((".py", ".md", ".txt")):
                continue
            path = self.repo / rel
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if "TODO" in line or "FIXME" in line:
                    hits.append(f"{rel}: {line.strip()[:120]}")
                    break
        return hits

    def _recommended_next_steps(self) -> list[str]:
        if any(t.status == TaskLifecycle.BLOCKED.value for t in self.attempted):
            return [
                "Review blocked tasks and rerun with --unsafe only if trusted and necessary."
            ]
        if any(
            t.status
            in {
                TaskLifecycle.FAILED.value,
                TaskLifecycle.RETRYABLE.value,
                TaskLifecycle.EXHAUSTED.value,
            }
            for t in self.attempted
        ):
            return [
                "Inspect verification findings, then rerun Villani mode with tighter wave limits."
            ]
        return ["Run full CI before merging autonomous changes."]

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
        return task.attempts > 1 and not task.intentional_changes and not task.validation_artifacts

    def _deterministic_followups(self, task: AutonomousTask, op: Opportunity) -> list[Opportunity]:
        followups: list[Opportunity] = []
        if task.title == "Validate baseline importability" and task.status == TaskLifecycle.PASSED.value:
            if self._category_state.get("tests") == "discovered":
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
            if self._category_state.get("entrypoints") == "discovered":
                followups.append(
                    Opportunity("Validate CLI entrypoint", "followup_entrypoint", 0.94, 0.84, op.affected_files, "entrypoints discovered during exploration", "small", "validate CLI entrypoint", TaskContract.VALIDATION.value)
                )
            if self._category_state.get("docs") == "discovered":
                followups.append(
                    Opportunity("Validate documented commands/examples", "followup_docs", 0.93, 0.82, ["README.md"], "docs present and may include runnable examples", "small", "validate documented commands/examples", TaskContract.INSPECTION.value)
                )
        return followups

    def _mark_category_discovery(self) -> None:
        files = [p.relative_to(self.repo).as_posix() for p in self.repo.rglob("*") if p.is_file()]
        if any(self._is_test_file(f) for f in files):
            self._category_state["tests"] = "discovered"
        if any(f.endswith(".md") for f in files):
            self._category_state["docs"] = "discovered"
        if any(f.endswith("cli.py") for f in files) or (self.repo / "pyproject.toml").exists():
            self._category_state["entrypoints"] = "discovered"
        if any(f.endswith(".py") for f in files):
            self._category_state["imports"] = "discovered"

    def _update_category_attempt_state(self, task: AutonomousTask) -> None:
        title = task.title.lower()
        if "test" in title:
            self._category_state["tests"] = "attempted"
        if "doc" in title:
            self._category_state["docs"] = "attempted"
        if "entrypoint" in title or "cli" in title:
            self._category_state["entrypoints"] = "attempted"
        if "import" in title:
            self._category_state["imports"] = "attempted"

    def _enqueue_surface_followups_if_needed(self) -> bool:
        inserted = False
        if self._category_state.get("tests") == "discovered":
            self._insert_followup(
                Opportunity("Run baseline tests", "followup_tests", 0.99, 0.9, ["tests/"], "tests remain unexamined", "small", "run baseline tests", TaskContract.VALIDATION.value),
                "system",
            )
            self._category_state["tests"] = "attempted"
            inserted = True
            self._log("[villani-mode] stop blocked: tests remain unexamined")
        if self._category_state.get("docs") == "discovered":
            self._insert_followup(
                Opportunity("Validate documented commands/examples", "followup_docs", 0.92, 0.78, ["README.md"], "docs remain unexamined", "small", "validate documented commands/examples", TaskContract.INSPECTION.value),
                "system",
            )
            self._category_state["docs"] = "attempted"
            inserted = True
        if self._category_state.get("entrypoints") == "discovered":
            self._insert_followup(
                Opportunity("Validate CLI entrypoint", "followup_entrypoint", 0.9, 0.76, [], "entrypoints remain unexamined", "small", "validate CLI entrypoint", TaskContract.VALIDATION.value),
                "system",
            )
            self._category_state["entrypoints"] = "attempted"
            inserted = True
        return inserted

    def _stop_reason_from_categories(self) -> str:
        self._stop_rationale = {
            "tests": self._category_state.get("tests", "unknown"),
            "docs": self._category_state.get("docs", "unknown"),
            "entrypoints": self._category_state.get("entrypoints", "unknown"),
            "improvements": "exhausted",
        }
        return (
            "No remaining opportunities above confidence threshold; "
            f"tests examined: {self._stop_rationale['tests']}; "
            f"docs examined: {self._stop_rationale['docs']}; "
            f"entrypoints examined: {self._stop_rationale['entrypoints']}."
        )

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
        return "\n".join(lines)
