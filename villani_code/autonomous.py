from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
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
from villani_code.execution import VILLANI_TASK_BUDGET
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


@dataclass(slots=True)
class AutonomousTask:
    task_id: str
    title: str
    rationale: str
    priority: float
    confidence: float
    verification_plan: list[str]
    task_contract: str = TaskContract.INSPECTION.value
    status: str = "pending"
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
        self._attempted_titles: set[str] = set()
        self.planner = TakeoverPlanner(self.repo)
        self.verifier = VerificationEngine(self.repo, logger=self._log)
        self.failure_classifier = FailureClassifier()
        self._preexisting_changes: set[str] = set()

    def run(self) -> dict[str, Any]:
        self._preexisting_changes = set(self._git_changed_files())
        state = TakeoverState(repo_summary=self.planner.build_repo_summary())
        self._emit(
            "takeover_dashboard",
            summary=state.repo_summary,
            wave=0,
            risk=state.current_risk_level,
        )
        state.discovered_opportunities = self.planner.discover_opportunities()
        self._emit(
            "takeover_ranked",
            count=len(state.discovered_opportunities),
            top=[o.title for o in state.discovered_opportunities[:5]],
        )

        if not state.discovered_opportunities:
            return self._build_takeover_summary(state, "No opportunities discovered.")

        for wave in range(1, self.takeover_config.max_waves + 1):
            remaining = [
                o
                for o in state.discovered_opportunities
                if o.confidence >= self.takeover_config.min_confidence
                and o.title not in self._attempted_titles
            ]
            if not remaining:
                return self._build_takeover_summary(
                    state, "No remaining opportunities above confidence threshold."
                )
            selected = remaining[: self.takeover_config.max_commands_per_wave]
            self._emit("autonomous_phase", phase=f"takeover wave {wave}")
            self._emit(
                "takeover_wave",
                wave=wave,
                selected=[o.title for o in selected],
                why="ranked by priority and confidence",
            )

            wave_files: set[str] = set()
            retired = 0
            for op in selected:
                task = AutonomousTask(
                    task_id=f"wave-{wave}-{retired + 1}",
                    title=op.title,
                    rationale=op.evidence,
                    priority=op.priority,
                    confidence=op.confidence,
                    verification_plan=["pytest -q tests/test_runner_defaults.py"]
                    if (self.repo / "tests").exists()
                    else [],
                    task_contract=op.task_contract,
                )
                self._attempted_titles.add(task.title)
                self._execute_task(task)
                task.files_changed = self._git_changed_files()
                task.intentional_changes, task.incidental_changes, _ = (
                    self._split_changes(task.files_changed)
                )
                task.produced_effect = bool(task.intentional_changes)
                wave_files.update(task.intentional_changes)
                self._emit(
                    "autonomous_phase",
                    phase=f"Intentional changes: {', '.join(task.intentional_changes) if task.intentional_changes else '[]'}",
                )
                if task.incidental_changes:
                    self._emit(
                        "autonomous_phase",
                        phase=f"Incidental changes: {', '.join(task.incidental_changes)}",
                    )

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
                if task.status == "passed":
                    retired += 1
                self.attempted.append(task)

            if len(wave_files) > self.takeover_config.max_files_per_wave:
                state.current_risk_level = "high"
                return self._build_takeover_summary(
                    state, "Blast radius exceeded configured max files per wave."
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

        return self._build_takeover_summary(
            state, "Reached maximum configured takeover waves."
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

    def _execute_task(self, task: AutonomousTask) -> None:
        task.status = "running"
        self._emit("autonomous_phase", phase=f"Villani mode task started: {task.title}")
        objective = (
            "You are in repo takeover mode. Execute one bounded intervention and summarize exact edits and validation. "
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
        if not task.validation_artifacts:
            task.validation_artifacts = [
                f"{cmd.get('command', '')} (exit={cmd.get('exit', 1)})"
                for cmd in task.verification_results
                if cmd.get("command")
            ]
        task.inspection_summary = str(execution.get("inspection_summary", "")).strip()
        task.runner_failures = list(
            execution.get("runner_failures", [])
        ) or self._extract_runner_failures(result)
        task.produced_effect = bool(task.intentional_changes)
        task.produced_validation = bool(task.validation_artifacts)
        task.produced_inspection_conclusion = bool(task.inspection_summary)
        task.completed = task.terminated_reason == "completed"
        task.status = "completed" if task.completed else "stopped"
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
        if task.files_changed:
            self._emit(
                "autonomous_phase",
                phase=f"Files changed: {', '.join(task.files_changed)}",
            )
        if self._transcript_contains_denied(result):
            task.status = "blocked"
            task.outcome += "\nBlocked by hard safety policy."
        if not self._has_any_evidence(task):
            task.outcome = (
                task.outcome + "\n" if task.outcome else ""
            ) + "No intervention or validation evidence produced."
            if task.status in {"completed", "stopped"}:
                task.status = "failed"

    def _adjudicate_task(
        self, task: AutonomousTask, verification: Any
    ) -> tuple[str, str]:
        if task.terminated_reason in {
            "no_edits",
            "recon_loop",
            "model_idle",
        } and not self._has_any_evidence(task):
            return (
                "failed",
                "no_effect: No intervention or validation evidence produced.",
            )

        if verification.status == VerificationStatus.UNCERTAIN:
            return (
                "failed",
                "verification_uncertain: task requires concrete evidence before pass.",
            )

        if not self._meets_contract(task):
            return (
                "failed",
                "contract_unsatisfied: no evidence produced for task contract.",
            )

        if task.runner_failures and not (
            task.produced_effect or task.produced_validation
        ):
            return (
                "failed",
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
                "failed",
                "runner_failure_blocked_pass: No intervention or validation evidence produced.",
            )

        if verification.status == VerificationStatus.PASS:
            return "passed", "passed"

        return "failed", "verification_failed"

    @staticmethod
    def _has_any_evidence(task: AutonomousTask) -> bool:
        return (
            task.produced_effect
            or task.produced_validation
            or task.produced_inspection_conclusion
        )

    def _meets_contract(self, task: AutonomousTask) -> bool:
        if task.task_contract == TaskContract.EFFECTFUL.value:
            return task.produced_effect and self._meets_effectful_minimum(task)
        if task.task_contract == TaskContract.VALIDATION.value:
            return task.produced_validation and self._meets_validation_minimum(task)
        return self._has_any_evidence(task)

    def _meets_effectful_minimum(self, task: AutonomousTask) -> bool:
        if task.title == "Bootstrap minimal tests":
            return any(self._is_test_file(path) for path in task.intentional_changes)
        if task.title == "Audit missing usage docs":
            return any(
                is_authoritative_doc_path(path) for path in task.intentional_changes
            )
        return True

    def _meets_validation_minimum(self, task: AutonomousTask) -> bool:
        if task.title == "Validate baseline importability":
            return bool(task.validation_artifacts)
        return task.produced_validation

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
            content = str(tr.get("content", ""))
            if "command:" in content and "exit:" in content:
                out.append(
                    {
                        "command": content.splitlines()[0]
                        .replace("command:", "")
                        .strip(),
                        "exit": 0 if "exit: 0" in content else 1,
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
                    "outcome": t.outcome[:1200],
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
            "blockers": [t.title for t in self.attempted if t.status == "blocked"],
            "done_reason": done_reason,
            "completed_waves": state.completed_waves,
            "recommended_next_steps": self._recommended_next_steps(),
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
        if any(t.status == "blocked" for t in self.attempted):
            return [
                "Review blocked tasks and rerun with --unsafe only if trusted and necessary."
            ]
        if any(t.status == "failed" for t in self.attempted):
            return [
                "Inspect verification findings, then rerun takeover with tighter wave limits."
            ]
        return ["Run full CI before merging autonomous changes."]

    def _git_changed_files(self) -> list[str]:
        proc = subprocess.run(
            ["git", "status", "--short"], cwd=self.repo, capture_output=True, text=True
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
        lines = ["# Repo takeover summary", ""]
        lines.append(f"Repo assessment: {summary.get('repo_summary', '')}")
        lines.append("## Tasks")
        for task in summary.get("tasks_attempted", []):
            lines.append(
                f"- {task['title']} :: {task['status']} ({task.get('task_contract', 'inspection')})"
            )
            if task.get("files_changed"):
                lines.append(
                    f"  - changed: {json.dumps(task.get('files_changed', []))}"
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
            if task.get("outcome") and task.get("status") != "passed":
                lines.append(f"  - reason: {task.get('outcome')[:180]}")
            for vr in task.get("verification", []):
                lines.append(f"  - verification: {json.dumps(vr)}")
        lines.append("")
        waves = summary.get("completed_waves", [])
        for wave in waves:
            lines.append(
                f"wave {wave.get('wave')}: retired={wave.get('retired')} confidence={wave.get('confidence')} files={len(wave.get('files_touched', []))}"
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
