from __future__ import annotations

import json
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
from villani_code.repo_map import build_structured_repo_map
from villani_code.repo_rules import (
    classify_repo_path,
    is_authoritative_doc_path,
    is_ignored_repo_path,
)
from villani_code.working_memory import WorkingMemorySnapshot, WorkingMemoryStore


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
    critic_verdict: str = ""
    validation_confidence: float = 0.0
    execution_confidence: float = 0.0


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
        self._no_progress_waves: int = 0
        self.memory_store = WorkingMemoryStore(self.repo)
        self._last_stop_detail: str = ""

    def run(self) -> dict[str, Any]:
        self._preexisting_changes = set(self._git_changed_files())
        state = TakeoverState(repo_summary=self.planner.build_repo_summary())
        repo_map = build_structured_repo_map(self.repo)
        self._emit("autonomous_phase", phase="explorer: repo assessment")
        self._emit(
            "takeover_dashboard",
            summary=state.repo_summary,
            wave=0,
            risk=state.current_risk_level,
        )

        discovered = self.planner.discover_opportunities()
        state.discovered_opportunities = discovered
        category_counts = self._category_counts(discovered)
        self._emit(
            "autonomous_phase",
            phase=(
                "[villani-mode] explorer found "
                f"{len(discovered)} candidates across "
                f"{', '.join(f'{k}={v}' for k, v in sorted(category_counts.items())) or 'none'}"
            ),
        )

        if self._repo_is_nontrivial(repo_map) and len(discovered) < 3:
            discovered.extend(self._planner_backfill_minimum(repo_map, discovered))

        stop_reason = "Villani mode budget exhausted."
        for wave in range(1, self.takeover_config.max_waves + 1):
            if self._attempt_counter >= self.takeover_config.max_total_task_attempts:
                stop_reason = "Villani mode budget exhausted."
                break

            self._emit("autonomous_phase", phase="planner: rank backlog")
            candidates = self._build_wave_candidates(discovered)
            self._emit(
                "takeover_ranked",
                count=len(candidates),
                top=[o.title for o in candidates[:5]],
            )
            if not candidates:
                stop_reason = self._build_stop_reason(repo_map, discovered)
                break

            selected = candidates[: self.takeover_config.max_commands_per_wave]
            self._emit(
                "autonomous_phase",
                phase=f"[villani-mode] planner selected: {selected[0].title}",
            )
            self._emit(
                "takeover_wave",
                wave=wave,
                selected=[o.title for o in selected],
                why="ranked by priority/confidence/risk",
            )

            wave_files: set[str] = set()
            retired = retryable = blocked = 0
            progressed = False
            for index, op in enumerate(selected, start=1):
                if self._attempt_counter >= self.takeover_config.max_total_task_attempts:
                    stop_reason = "Villani mode budget exhausted."
                    break
                task = self._run_single_task(wave, index, op)
                self.attempted.append(task)
                if task.status == TaskLifecycle.PASSED.value:
                    retired += 1
                elif task.status == TaskLifecycle.RETRYABLE.value:
                    retryable += 1
                elif task.status == TaskLifecycle.BLOCKED.value:
                    blocked += 1
                if task.intentional_changes or task.validation_artifacts or task.produced_inspection_conclusion:
                    progressed = True
                wave_files.update(task.intentional_changes)

            if wave_files and not self._wave_has_validation_artifact(self.attempted[-len(selected) :]):
                self._followup_queue.append(self._validate_recent_changes_followup(sorted(wave_files)))

            if not progressed:
                self._no_progress_waves += 1
                self._emit("autonomous_phase", phase="critic: no-op wave detected; biasing toward investigation")
            else:
                self._no_progress_waves = 0

            avg_conf = round(sum(t.confidence for t in self.attempted[-len(selected):]) / max(1, len(selected)), 2)
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
            self._emit("takeover_wave_complete", wave=wave, retired=retired, confidence=avg_conf, risk=state.current_risk_level)

            discovered = self.planner.discover_opportunities()
            discovered.extend(self._followup_policy(repo_map, self.attempted[-len(selected):]))
            if self._repo_is_nontrivial(repo_map) and len(discovered) < 3:
                discovered.extend(self._planner_backfill_minimum(repo_map, discovered))

            if self._no_progress_waves >= 2:
                self._last_stop_detail = "Repeated no-progress waves; critic flagged fake autonomy patterns."
                stop_reason = "No remaining opportunities above confidence threshold."
                break

        summary = self._build_takeover_summary(state, stop_reason)
        self._write_working_memory(summary, discovered)
        self._emit("autonomous_phase", phase=f"[villani-mode] stop: {summary.get('done_reason', stop_reason)}")
        return summary

    def _run_single_task(self, wave: int, index: int, op: Opportunity) -> AutonomousTask:
        before_dirty = set(self._git_changed_files())
        task_key = self._task_key_for_opportunity(op)
        attempts = self._lineage_attempts.get(task_key, 0) + 1
        task = AutonomousTask(
            task_id=f"wave-{wave}-{index}",
            title=op.title,
            rationale=op.evidence,
            priority=op.priority,
            confidence=op.confidence,
            verification_plan=op.validation_strategy or (["pytest -q"] if (self.repo / "tests").exists() else []),
            task_contract=op.task_contract,
            task_key=task_key,
            parent_task_key=self._parent_task_key(op),
            origin_kind=op.category,
            attempts=attempts,
            retries=max(0, attempts - 1),
        )
        self._lineage_attempts[task_key] = attempts
        self._attempt_counter += 1

        self._emit("autonomous_phase", phase="builder: executing bounded task")
        self._execute_task(task)

        after_dirty = set(self._git_changed_files())
        task.files_changed = sorted(after_dirty - before_dirty)
        delta_basis = task.files_changed or task.intentional_changes or task.incidental_changes
        task.intentional_changes, task.incidental_changes, _ = self._split_changes(delta_basis)
        if not task.files_changed:
            task.files_changed = sorted(set(task.intentional_changes) | set(task.incidental_changes))
        task.produced_effect = bool(task.intentional_changes)

        verification = self.verifier.verify(
            op.proposed_next_action,
            task.intentional_changes,
            task.verification_results,
            validation_artifacts=task.validation_artifacts,
            intended_targets=task.intended_targets,
            before_contents=task.before_contents,
        )
        task.validation_confidence = verification.confidence_score
        task.execution_confidence = round((task.confidence + verification.confidence_score) / 2, 2)
        task.verification_results.append(
            {
                "summary": verification.summary,
                "status": verification.status.value,
                "confidence": verification.confidence_score,
                "findings": [f"{f.category.value}: {f.message}" for f in verification.findings],
            }
        )
        task.status, task.outcome = self._adjudicate_task(task, verification)
        task.status = self._update_lifecycle_after_attempt(task, op)
        task.critic_verdict = self._critic_verdict(task, verification)
        self._emit("autonomous_phase", phase=f"critic: {task.critic_verdict}")
        return task

    def _category_counts(self, opportunities: list[Opportunity]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for op in opportunities:
            counts[op.category] = counts.get(op.category, 0) + 1
        return counts

    def _repo_is_nontrivial(self, repo_map: Any) -> bool:
        return bool(repo_map.key_modules or repo_map.tests or len(repo_map.docs) > 1)

    def _planner_backfill_minimum(self, repo_map: Any, discovered: list[Opportunity]) -> list[Opportunity]:
        titles = {op.title for op in discovered}
        out: list[Opportunity] = []
        if "Validate baseline importability" not in titles and repo_map.key_modules:
            out.append(
                Opportunity(
                    title="Validate baseline importability",
                    category="validation",
                    rationale="backfill minimum backlog for nontrivial repo",
                    priority=0.72,
                    confidence=0.75,
                    evidence_items=repo_map.key_modules[:2],
                    estimated_risk=0.2,
                    estimated_value=0.8,
                    validation_strategy=["python -c 'import villani_code'"],
                    followup_candidates=["Run baseline tests"],
                    affected_files=repo_map.key_modules[:2],
                    evidence="backfill validation candidate",
                    blast_radius="small",
                    proposed_next_action="run bounded import validation",
                    task_contract=TaskContract.VALIDATION.value,
                )
            )
        if "Run baseline tests" not in titles and repo_map.tests:
            out.append(
                Opportunity(
                    title="Run baseline tests",
                    category="validation",
                    rationale="tests detected; baseline should run early",
                    priority=0.8,
                    confidence=0.8,
                    evidence_items=repo_map.tests[:2],
                    estimated_risk=0.25,
                    estimated_value=0.85,
                    validation_strategy=["pytest -q"],
                    followup_candidates=["Validate CLI entrypoints"],
                    affected_files=repo_map.tests[:2],
                    evidence="tests detected",
                    blast_radius="small",
                    proposed_next_action="run pytest -q",
                    task_contract=TaskContract.VALIDATION.value,
                )
            )
        if "Investigate docs/code mismatch" not in titles and repo_map.docs:
            out.append(
                Opportunity(
                    title="Investigate docs/code mismatch",
                    category="investigation",
                    rationale="docs exist; at least one docs/code check should be considered",
                    priority=0.62,
                    confidence=0.56,
                    evidence_items=repo_map.docs[:2],
                    estimated_risk=0.1,
                    estimated_value=0.62,
                    validation_strategy=["inspect docs command against code"],
                    followup_candidates=["Validate documented commands"],
                    affected_files=repo_map.docs[:2],
                    evidence="docs exist",
                    blast_radius="small",
                    proposed_next_action="inspect docs and command references",
                    task_contract=TaskContract.INSPECTION.value,
                )
            )
        return out[: max(0, 3 - len(discovered))]

    def _build_stop_reason(self, repo_map: Any, discovered: list[Opportunity]) -> str:
        reasons: list[str] = []
        if not repo_map.tests:
            reasons.append("no tests were found")
        if not repo_map.entrypoints:
            reasons.append("no entrypoints were found")
        if not repo_map.docs:
            reasons.append("no actionable docs were found")
        if not repo_map.todo_hits and not repo_map.import_hotspots:
            reasons.append("no suspicious hotspots were found")
        if discovered and not self._build_wave_candidates(discovered):
            reasons.append("safe candidates were filtered by confidence/risk thresholds")
        if not reasons:
            reasons.append("backlog exhausted after replanning")
        self._last_stop_detail = "; ".join(reasons)
        return "No remaining opportunities above confidence threshold."

    def _followup_policy(self, repo_map: Any, recent_tasks: list[AutonomousTask]) -> list[Opportunity]:
        followups: list[Opportunity] = []
        for task in recent_tasks:
            if task.title == "Validate baseline importability" and task.status == TaskLifecycle.PASSED.value:
                if repo_map.tests:
                    followups.append(
                        Opportunity(
                            title="Run baseline tests",
                            category="validation",
                            priority=0.86,
                            confidence=0.83,
                            affected_files=repo_map.tests[:2],
                            evidence="follow-up tests",
                            blast_radius="small",
                            proposed_next_action="run pytest -q",
                            task_contract=TaskContract.VALIDATION.value,
                            rationale="follow-up policy after import success",
                            evidence_items=repo_map.tests[:2],
                            estimated_risk=0.2,
                            estimated_value=0.9,
                            validation_strategy=["pytest -q"],
                        )
                    )
                if repo_map.entrypoints:
                    followups.append(
                        Opportunity(
                            title="Validate CLI entrypoints",
                            category="validation",
                            priority=0.72,
                            confidence=0.72,
                            affected_files=repo_map.entrypoints[:2],
                            evidence="cli surfaces detected",
                            blast_radius="small",
                            proposed_next_action="execute entrypoint --help",
                            task_contract=TaskContract.VALIDATION.value,
                            rationale="follow-up policy after import success",
                            evidence_items=repo_map.entrypoints[:2],
                            estimated_risk=0.2,
                            estimated_value=0.72,
                            validation_strategy=["python -m <entrypoint> --help"],
                        )
                    )
                if repo_map.doc_commands:
                    followups.append(
                        Opportunity(
                            title="Validate documented commands",
                            category="validation",
                            priority=0.7,
                            confidence=0.68,
                            affected_files=[],
                            evidence="docs command examples detected",
                            blast_radius="small",
                            proposed_next_action="execute one docs command",
                            task_contract=TaskContract.VALIDATION.value,
                            rationale="follow-up policy after import success",
                            evidence_items=repo_map.doc_commands[:2],
                            estimated_risk=0.2,
                            estimated_value=0.74,
                            validation_strategy=[repo_map.doc_commands[0]],
                        )
                    )

            if task.status == TaskLifecycle.FAILED.value and not task.validation_artifacts:
                followups.append(
                    Opportunity(
                        title=f"Investigate failure scope: {task.title}",
                        category="investigation",
                        priority=0.66,
                        confidence=0.58,
                        affected_files=task.files_changed[:2],
                        evidence="ambiguous failure",
                        blast_radius="small",
                        proposed_next_action="gather targeted evidence only",
                        task_contract=TaskContract.INSPECTION.value,
                        rationale="failed validation needs narrower investigation before retry",
                        evidence_items=[task.outcome[:120]],
                        estimated_risk=0.1,
                        estimated_value=0.63,
                        validation_strategy=["inspect failure logs and narrow scope"],
                        followup_candidates=[f"Retry {task.title} with narrowed command"],
                    )
                )
        return followups

    def _critic_verdict(self, task: AutonomousTask, verification: Any) -> str:
        if not (task.intentional_changes or task.validation_artifacts or task.produced_inspection_conclusion):
            return "no-progress wave: task looked complete but produced no meaningful execution evidence"
        if verification.repeated_verification_state:
            return "retryable: repeated validation state detected, strategy change required"
        if task.status == TaskLifecycle.PASSED.value:
            if task.produced_validation:
                return "retired: task validated with concrete receipts"
            return "retired: task intentionally exhausted with inspection conclusion"
        if task.status == TaskLifecycle.BLOCKED.value:
            return "blocked: external or repository blocker prevented progress"
        if task.status == TaskLifecycle.RETRYABLE.value:
            return "retryable: follow-up strategy available"
        if task.status == TaskLifecycle.EXHAUSTED.value:
            return "superseded/exhausted: retries spent without sufficient evidence"
        return "needs follow-up: weak evidence or unresolved verification"

    def _write_working_memory(self, summary: dict[str, Any], discovered: list[Opportunity]) -> None:
        snapshot = WorkingMemorySnapshot(
            repo_assessment={"summary": summary.get("repo_summary", "")},
            backlog=[{"title": op.title, "category": op.category, "confidence": op.confidence} for op in discovered[:12]],
            completed_tasks=[t for t in summary.get("tasks_attempted", []) if t.get("status") == TaskLifecycle.PASSED.value],
            failed_tasks=[t for t in summary.get("tasks_attempted", []) if t.get("status") != TaskLifecycle.PASSED.value],
            validation_receipts=[a for t in summary.get("tasks_attempted", []) for a in t.get("validation_artifacts", [])][:30],
            changed_files=summary.get("files_changed", []),
            blockers=summary.get("blockers", []),
            rejected_hypotheses=[t.get("title", "") for t in summary.get("tasks_attempted", []) if t.get("status") in {TaskLifecycle.EXHAUSTED.value, TaskLifecycle.BLOCKED.value}],
            next_recommended_actions=summary.get("recommended_next_steps", []),
            stop_reason=summary.get("done_reason", ""),
        )
        self.memory_store.write(snapshot)

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

    def _update_lifecycle_after_attempt(
        self, task: AutonomousTask, op: Opportunity
    ) -> str:
        task_key = task.task_key
        retries_used = self._lineage_retries.get(task_key, 0)
        retry_limit = self._retry_limit_for_contract(task.task_contract)

        if task.status == TaskLifecycle.PASSED.value:
            self._lineage_status[task_key] = TaskLifecycle.PASSED.value
            return TaskLifecycle.PASSED.value

        if task.status == TaskLifecycle.BLOCKED.value or any(
            "permission" in f.lower() or "denied" in f.lower()
            for f in task.runner_failures
        ):
            self._lineage_status[task_key] = TaskLifecycle.BLOCKED.value
            self._lineage_blockers[task_key] = "; ".join(task.runner_failures[:2])
            self._followup_queue.extend(self._generate_followups(task, op))
            return TaskLifecycle.BLOCKED.value

        actionable = self._is_actionable_failure(task)
        if actionable:
            self._followup_queue.extend(self._generate_followups(task, op))

        if retries_used < retry_limit:
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
            objective += (
                "\nValidation contract (mandatory): inspect relevant package layout first, then run one bounded import validation command "
                "(prefer python -c or short heredoc), capture the executed command output and exit code, and only then summarize result. "
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
                    "critic_verdict": t.critic_verdict,
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
            "stop_detail": self._last_stop_detail,
            "backlog_count": len(state.discovered_opportunities),
            "selected_tasks": [t.title for t in self.attempted],
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
        lines.append("## Backlog")
        lines.append(f"- backlog_count: {summary.get('backlog_count', 0)}")
        lines.append(f"- selected_tasks: {json.dumps(summary.get('selected_tasks', []))}")
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
        if summary.get("stop_detail"):
            lines.append(f"Stop detail: {summary.get('stop_detail')}")
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
