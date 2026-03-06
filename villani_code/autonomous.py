from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from villani_code.autonomy import FailureClassifier, Opportunity, TakeoverConfig, TakeoverPlanner, TakeoverState, VerificationEngine, VerificationStatus


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

    def __init__(self, runner: Any, repo: Path, steering_objective: str | None = None, event_callback: Callable[[dict[str, Any]], None] | None = None, takeover_config: TakeoverConfig | None = None) -> None:
        self.runner = runner
        self.repo = repo.resolve()
        self.steering_objective = steering_objective
        self.event_callback = event_callback or (lambda _event: None)
        self.takeover_config = takeover_config or TakeoverConfig()
        self.attempted: list[AutonomousTask] = []
        self._attempted_titles: set[str] = set()
        self.planner = TakeoverPlanner(self.repo)
        self.verifier = VerificationEngine(self.repo)
        self.failure_classifier = FailureClassifier()

    def run(self) -> dict[str, Any]:
        state = TakeoverState(repo_summary=self.planner.build_repo_summary())
        self._emit("takeover_dashboard", summary=state.repo_summary, wave=0, risk=state.current_risk_level)
        state.discovered_opportunities = self.planner.discover_opportunities()
        self._emit("takeover_ranked", count=len(state.discovered_opportunities), top=[o.title for o in state.discovered_opportunities[:5]])

        for wave in range(1, self.takeover_config.max_waves + 1):
            remaining = [o for o in state.discovered_opportunities if o.confidence >= self.takeover_config.min_confidence and o.title not in self._attempted_titles]
            if not remaining:
                return self._build_takeover_summary(state, "No remaining opportunities above confidence threshold.")
            selected = remaining[: self.takeover_config.max_commands_per_wave]
            self._emit("autonomous_phase", phase=f"takeover wave {wave}")
            self._emit("takeover_wave", wave=wave, selected=[o.title for o in selected], why="ranked by priority and confidence")

            wave_files: set[str] = set()
            retired = 0
            for op in selected:
                task = AutonomousTask(
                    task_id=f"wave-{wave}-{retired+1}",
                    title=op.title,
                    rationale=op.evidence,
                    priority=op.priority,
                    confidence=op.confidence,
                    verification_plan=["python -m compileall -q ."] if (self.repo / "pyproject.toml").exists() else [],
                )
                self._attempted_titles.add(task.title)
                self._execute_task(task)
                task.files_changed = self._git_changed_files()
                wave_files.update(task.files_changed)

                verification = self.verifier.verify(op.proposed_next_action, task.files_changed, task.verification_results)
                task.verification_results.append({"summary": verification.summary, "status": verification.status.value, "confidence": verification.confidence_score, "findings": [f"{f.category.value}: {f.message}" for f in verification.findings]})
                if verification.status in {VerificationStatus.PASS, VerificationStatus.UNCERTAIN}:
                    task.status = "passed" if verification.status == VerificationStatus.PASS else "uncertain"
                    retired += 1
                else:
                    task.status = "failed"
                    event = self.failure_classifier.classify("verification failure", verification.summary)
                    self._emit("failure_classified", category=event.category.value, summary=event.cause_summary, next_strategy=event.suggested_strategy)
                self.attempted.append(task)

            if len(wave_files) > self.takeover_config.max_files_per_wave:
                state.current_risk_level = "high"
                return self._build_takeover_summary(state, "Blast radius exceeded configured max files per wave.")

            avg_conf = round(sum(t.confidence for t in self.attempted[-len(selected) :]) / max(1, len(selected)), 2)
            state.completed_waves.append({"wave": wave, "retired": retired, "confidence": avg_conf, "files_touched": sorted(wave_files)})
            self._emit("takeover_wave_complete", wave=wave, retired=retired, confidence=avg_conf, risk=state.current_risk_level)

        return self._build_takeover_summary(state, "Reached maximum configured takeover waves.")

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
        for op in self.planner.discover_opportunities():
            candidates.append(AutonomousTask(task_id=op.title.lower().replace(" ", "-"), title=op.title, rationale=op.evidence, priority=op.priority, confidence=op.confidence, verification_plan=[op.proposed_next_action]))
        self._emit("autonomous_candidates", count=len(candidates), tasks=[c.title for c in candidates])
        return candidates

    def rank_tasks(self, tasks: list[AutonomousTask]) -> list[AutonomousTask]:
        ranked = sorted(tasks, key=lambda t: (t.priority * 0.7 + t.confidence * 0.3), reverse=True)
        self._emit("autonomous_phase", phase="ranking tasks", ranked=[t.title for t in ranked])
        return ranked

    def _execute_task(self, task: AutonomousTask) -> None:
        task.status = "running"
        objective = (
            "You are in repo takeover mode. Execute one bounded intervention and summarize exact edits and validation. "
            f"Intervention: {task.title}\nEvidence: {task.rationale}"
        )
        result = self.runner.run(objective)
        task.outcome = "\n".join(block.get("text", "") for block in result.get("response", {}).get("content", []) if block.get("type") == "text")
        task.files_changed = self._git_changed_files()
        task.verification_results = self._extract_commands(result)
        if self._transcript_contains_denied(result):
            task.status = "blocked"
            task.outcome += "\nBlocked by hard safety policy."

    def _extract_commands(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tr in result.get("transcript", {}).get("tool_results", []):
            content = str(tr.get("content", ""))
            if "command:" in content and "exit:" in content:
                out.append({"command": content.splitlines()[0].replace("command:", "").strip(), "exit": 0 if "exit: 0" in content else 1})
        return out

    def _build_takeover_summary(self, state: TakeoverState, done_reason: str) -> dict[str, Any]:
        return {
            "repo_summary": state.repo_summary,
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
            "completed_waves": state.completed_waves,
            "recommended_next_steps": self._recommended_next_steps(),
        }

    def _detect_tooling_commands(self, files: list[str]) -> list[str]:
        commands: list[str] = []
        if "pyproject.toml" in files:
            commands.append("python -m compileall -q .")
        if any(f.startswith("tests/") for f in files):
            commands.append("pytest -q")
        return commands or ["python -m compileall -q ."]

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
            return ["Review blocked tasks and rerun with --unsafe only if trusted and necessary."]
        if any(t.status == "failed" for t in self.attempted):
            return ["Inspect verification findings, then rerun takeover with tighter wave limits."]
        return ["Run full CI before merging autonomous changes."]

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
        lines = ["# Repo takeover summary", ""]
        lines.append(f"Repo assessment: {summary.get('repo_summary', '')}")
        lines.append("## Tasks")
        for task in summary.get("tasks_attempted", []):
            lines.append(f"- {task['title']} :: {task['status']}")
            for vr in task.get("verification", []):
                lines.append(f"  - verification: {json.dumps(vr)}")
        lines.append("")
        waves = summary.get("completed_waves", [])
        for wave in waves:
            lines.append(f"wave {wave.get('wave')}: retired={wave.get('retired')} confidence={wave.get('confidence')} files={len(wave.get('files_touched', []))}")
        lines.append(f"Done reason: {summary.get('done_reason', '')}")
        lines.append(f"Blockers: {json.dumps(summary.get('blockers', []))}")
        lines.append(f"Files changed: {json.dumps(summary.get('files_changed', []))}")
        for step in summary.get("recommended_next_steps", []):
            lines.append(f"Next: {step}")
        return "\n".join(lines)
