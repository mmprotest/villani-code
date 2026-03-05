from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


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
    status: str = "pending"
    outcome: str = ""
    files_changed: list[str] = field(default_factory=list)
    verification_results: list[dict[str, Any]] = field(default_factory=list)


class VillaniModeController:
    """Deterministic autonomous repo-improvement loop for Villani mode."""

    def __init__(self, runner: Any, repo: Path, steering_objective: str | None = None, event_callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.runner = runner
        self.repo = repo.resolve()
        self.steering_objective = steering_objective
        self.event_callback = event_callback or (lambda _event: None)
        self.attempted: list[AutonomousTask] = []
        self._attempted_titles: set[str] = set()

    def run(self) -> dict[str, Any]:
        self._emit("autonomous_phase", phase="scanning repo")
        while True:
            snapshot = self.inspect_repo()
            tasks = self.generate_candidates(snapshot)
            ranked = self.rank_tasks(tasks)
            remaining = [t for t in ranked if t.title not in self._attempted_titles and self._is_worthwhile(t)]
            if not remaining:
                summary = self._build_final_summary("No clearly worthwhile, strongly verifiable tasks remain.")
                self._emit("autonomous_phase", phase="summarizing")
                return summary

            task = remaining[0]
            self._attempted_titles.add(task.title)
            self._emit("autonomous_phase", phase="editing", task=task.title)
            self._execute_task(task)
            self._emit("autonomous_phase", phase="verifying", task=task.title)
            self._verify_task(task)
            self.attempted.append(task)

    def inspect_repo(self) -> RepoSnapshot:
        files = sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob("*") if p.is_file() and ".git/" not in p.as_posix())
        key = [f for f in files if f in {"README.md", "pyproject.toml", "getting-started.md"} or f.startswith("villani_code/")][:40]
        docs = [f for f in files if f.startswith("docs/") or f.endswith(".md")][:40]
        tests = [f for f in files if f.startswith("tests/")][:80]
        config = [f for f in files if f.endswith((".toml", ".yaml", ".yml", ".json"))][:60]
        ci = [f for f in files if ".github/workflows/" in f or f.startswith(".github/")][:20]
        todos = self._todo_hits(files)
        cmds = self._detect_tooling_commands(files)
        self._emit("autonomous_scan", files_inspected=len(files), key_files=key[:10], tooling_commands=cmds)
        return RepoSnapshot(key_files=key, docs=docs, tests=tests, config_files=config, ci_files=ci, tooling_commands=cmds, todo_hits=todos)

    def generate_candidates(self, snapshot: RepoSnapshot) -> list[AutonomousTask]:
        candidates: list[AutonomousTask] = []
        check_failures = self._run_discovery_checks(snapshot.tooling_commands)
        for cmd, result in check_failures:
            if result["exit"] != 0:
                candidates.append(
                    AutonomousTask(
                        task_id=f"fix-{cmd}",
                        title=f"Fix failing check: {cmd}",
                        rationale="A failing local verification command indicates a concrete quality gap.",
                        priority=0.95,
                        confidence=0.85,
                        verification_plan=[cmd],
                    )
                )

        if snapshot.todo_hits:
            candidates.append(
                AutonomousTask(
                    task_id="todo-triage",
                    title="Resolve highest-signal TODO/FIXME items",
                    rationale="TODO/FIXME markers often indicate incomplete implementation or docs drift.",
                    priority=0.55,
                    confidence=0.6,
                    verification_plan=snapshot.tooling_commands[:1] or ["python -m compileall -q ."],
                )
            )

        if not candidates:
            candidates.append(
                AutonomousTask(
                    task_id="docs-sync",
                    title="Improve documentation consistency for current behavior",
                    rationale="Repository has no high-confidence failing checks, so docs consistency is the safest verifiable improvement.",
                    priority=0.4,
                    confidence=0.55,
                    verification_plan=snapshot.tooling_commands[:1] or ["python -m compileall -q ."],
                )
            )

        if self.steering_objective:
            for task in candidates:
                if self.steering_objective.lower() in task.title.lower() or self.steering_objective.lower() in task.rationale.lower():
                    task.priority += 0.2

        self._emit("autonomous_candidates", count=len(candidates), tasks=[c.title for c in candidates])
        return candidates

    def rank_tasks(self, tasks: list[AutonomousTask]) -> list[AutonomousTask]:
        ranked = sorted(tasks, key=lambda t: (t.priority * 0.7 + t.confidence * 0.3), reverse=True)
        self._emit("autonomous_phase", phase="ranking tasks", ranked=[t.title for t in ranked])
        return ranked

    def _execute_task(self, task: AutonomousTask) -> None:
        task.status = "running"
        objective = (
            "You are running in Villani mode autonomous execution. "
            "Work directly on this repository to complete the task below, then summarize edits and intended verification. "
            "Do not ask the user for permission for normal local repo operations. "
            "Task: "
            f"{task.title}\nRationale: {task.rationale}"
        )
        if self.steering_objective:
            objective += f"\nSteering objective: {self.steering_objective}"
        result = self.runner.run(objective)
        task.outcome = "\n".join(block.get("text", "") for block in result.get("response", {}).get("content", []) if block.get("type") == "text")
        task.files_changed = self._git_changed_files()
        if self._transcript_contains_denied(result):
            task.status = "blocked"
            task.outcome += "\nBlocked by hard safety policy."

    def _verify_task(self, task: AutonomousTask) -> None:
        results: list[dict[str, Any]] = []
        for command in task.verification_plan:
            proc = subprocess.run(command, cwd=self.repo, shell=True, capture_output=True, text=True)
            results.append({"command": command, "exit": proc.returncode, "stdout": proc.stdout[:1200], "stderr": proc.stderr[:800]})
        task.verification_results = results
        if task.status == "blocked":
            return
        if results and all(r["exit"] == 0 for r in results):
            task.status = "passed"
        elif results and any(r["exit"] == 0 for r in results):
            task.status = "partially_verified"
        else:
            task.status = "failed"

    def _is_worthwhile(self, task: AutonomousTask) -> bool:
        return task.confidence >= 0.5 and bool(task.verification_plan)

    def _build_final_summary(self, done_reason: str) -> dict[str, Any]:
        return {
            "tasks_attempted": [
                {
                    "id": t.task_id,
                    "title": t.title,
                    "status": t.status,
                    "verification": t.verification_results,
                    "files_changed": t.files_changed,
                    "outcome": t.outcome[:1200],
                }
                for t in self.attempted
            ],
            "files_changed": self._git_changed_files(),
            "blockers": [t.title for t in self.attempted if t.status == "blocked"],
            "done_reason": done_reason,
            "recommended_next_steps": self._recommended_next_steps(),
        }

    def _detect_tooling_commands(self, files: list[str]) -> list[str]:
        commands: list[str] = []
        if "pyproject.toml" in files:
            commands.append("python -m compileall -q .")
        if any(f.startswith("tests/") for f in files):
            commands.append("pytest -q")
        return commands or ["python -m compileall -q ."]

    def _run_discovery_checks(self, commands: list[str]) -> list[tuple[str, dict[str, Any]]]:
        checks: list[tuple[str, dict[str, Any]]] = []
        for cmd in commands[:2]:
            proc = subprocess.run(cmd, cwd=self.repo, shell=True, capture_output=True, text=True)
            checks.append((cmd, {"exit": proc.returncode, "stdout": proc.stdout[:600], "stderr": proc.stderr[:400]}))
        return checks

    def _todo_hits(self, files: list[str]) -> list[str]:
        hits: list[str] = []
        for rel in files:
            if len(hits) >= 20:
                break
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
            return ["Review blocked tasks and rerun with --unsafe only if you trust the commands and need broader shell operations."]
        if any(t.status == "failed" for t in self.attempted):
            return ["Review failed verification commands and re-run Villani mode after resolving environment issues."]
        return ["Run a full CI pipeline before merging autonomous changes."]

    def _git_changed_files(self) -> list[str]:
        proc = subprocess.run(["git", "status", "--short"], cwd=self.repo, capture_output=True, text=True)
        changed: list[str] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            changed.append(line[3:].strip())
        return changed

    def _transcript_contains_denied(self, result: dict[str, Any]) -> bool:
        transcript = result.get("transcript", {})
        for tool_result in transcript.get("tool_results", []):
            content = str(tool_result.get("content", ""))
            if "Denied by permission policy" in content or "Refusing command" in content:
                return True
        return False

    def _emit(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type}
        event.update(payload)
        self.event_callback(event)

    @staticmethod
    def format_summary(summary: dict[str, Any]) -> str:
        lines = ["# Villani mode summary", ""]
        lines.append("## Tasks")
        for task in summary.get("tasks_attempted", []):
            lines.append(f"- {task['title']} :: {task['status']}")
            for vr in task.get("verification", []):
                lines.append(f"  - verify `{vr['command']}` => exit {vr['exit']}")
        lines.append("")
        lines.append(f"Done reason: {summary.get('done_reason', '')}")
        blockers = summary.get("blockers", [])
        lines.append(f"Blockers: {json.dumps(blockers)}")
        lines.append(f"Files changed: {json.dumps(summary.get('files_changed', []))}")
        for step in summary.get("recommended_next_steps", []):
            lines.append(f"Next: {step}")
        return "\n".join(lines)
