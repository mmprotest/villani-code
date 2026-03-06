from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class PlanRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(slots=True)
class ExecutionPlan:
    task_goal: str
    assumptions: list[str]
    relevant_files: list[str]
    proposed_actions: list[str]
    risks: list[str]
    validation_steps: list[str]
    done_criteria: list[str]
    risk_level: PlanRiskLevel
    non_trivial: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_level"] = self.risk_level.value
        return payload

    def to_human_text(self) -> str:
        parts = [
            "Execution plan:",
            f"- task_goal: {self.task_goal}",
            f"- risk_level: {self.risk_level.value}",
            f"- non_trivial: {self.non_trivial}",
            f"- assumptions: {', '.join(self.assumptions) if self.assumptions else 'none'}",
            f"- relevant_files: {', '.join(self.relevant_files) if self.relevant_files else 'none'}",
            "- proposed_actions:",
        ]
        parts.extend(f"  - {a}" for a in self.proposed_actions)
        parts.append("- risks:")
        parts.extend(f"  - {r}" for r in self.risks)
        parts.append("- validation_steps:")
        parts.extend(f"  - {v}" for v in self.validation_steps)
        parts.append("- done_criteria:")
        parts.extend(f"  - {d}" for d in self.done_criteria)
        return "\n".join(parts)


@dataclass(slots=True)
class PlanAnalysis:
    touches_multiple_files: bool = False
    dependency_change: bool = False
    migration_like: bool = False
    refactor_like: bool = False
    test_fix_requires_edits: bool = False
    destructive_shell: bool = False


def _keyword_hit(text: str, words: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(w in lower for w in words)


def classify_plan_risk(instruction: str, analysis: PlanAnalysis) -> PlanRiskLevel:
    text = instruction.lower()
    if analysis.destructive_shell or _keyword_hit(text, ("delete", "rm -rf", "rewrite history", "force-push")):
        return PlanRiskLevel.HIGH
    if analysis.dependency_change or analysis.migration_like or _keyword_hit(text, ("upgrade dep", "lockfile", "migration")):
        return PlanRiskLevel.HIGH
    if analysis.touches_multiple_files or analysis.refactor_like or analysis.test_fix_requires_edits:
        return PlanRiskLevel.MEDIUM
    return PlanRiskLevel.LOW


def is_non_trivial_task(instruction: str, analysis: PlanAnalysis) -> bool:
    text = instruction.lower()
    if any(
        [
            analysis.touches_multiple_files,
            analysis.refactor_like,
            analysis.dependency_change,
            analysis.migration_like,
            analysis.test_fix_requires_edits,
            analysis.destructive_shell,
        ]
    ):
        return True
    return _keyword_hit(
        text,
        (
            "refactor",
            "dependency",
            "dependencies",
            "migrate",
            "migration",
            "fix failing tests",
            "run tests and fix",
            "edit files",
            "apply patch",
            "multi-file",
        ),
    )


def generate_execution_plan(
    instruction: str,
    repo: Path,
    repo_map: dict[str, Any] | None,
    validation_steps: list[str] | None,
) -> ExecutionPlan:
    text = instruction.strip()
    analysis = PlanAnalysis(
        touches_multiple_files=_keyword_hit(text, ("multiple files", "across", "repo-wide", "project-wide")),
        dependency_change=_keyword_hit(text, ("dependency", "dependencies", "package", "requirements", "pyproject", "package.json")),
        migration_like=_keyword_hit(text, ("migrate", "migration", "schema", "database")),
        refactor_like=_keyword_hit(text, ("refactor", "restructure", "rename")),
        test_fix_requires_edits=_keyword_hit(text, ("fix failing tests", "fix tests", "make tests pass")),
        destructive_shell=_keyword_hit(text, ("rm ", "git reset --hard", "git rebase", "force push")),
    )
    non_trivial = is_non_trivial_task(text, analysis)
    risk = classify_plan_risk(text, analysis)

    relevant_files = []
    if repo_map:
        relevant_files.extend(repo_map.get("manifests", [])[:3])
        relevant_files.extend(repo_map.get("config_files", [])[:3])
        relevant_files.extend(repo_map.get("source_roots", [])[:2])
    relevant_files = sorted(dict.fromkeys(relevant_files))

    actions = ["Inspect relevant source and tests before edits"]
    if non_trivial:
        actions.append("Implement the smallest viable code changes aligned with the task goal")
        actions.append("Run validation loop and repair failures if needed")
    else:
        actions.append("Perform read-only analysis and summarize findings")

    risks = ["Potential regressions in touched modules"] if non_trivial else ["Minimal; read-only or narrowly scoped"]
    if risk is PlanRiskLevel.HIGH:
        risks.append("High-impact operation detected; require explicit confirmation gate")

    validation = validation_steps or ["Run configured validation steps in cost-aware order"]
    done = [
        "Task goal satisfied",
        "Validation passes (or unresolved failures clearly reported)",
        "Summary includes changed files and outcomes",
    ]

    return ExecutionPlan(
        task_goal=text,
        assumptions=["Repository is in a valid local git checkout", "Configured tools are available in PATH"],
        relevant_files=relevant_files,
        proposed_actions=actions,
        risks=risks,
        validation_steps=validation,
        done_criteria=done,
        risk_level=risk,
        non_trivial=non_trivial,
    )


def compact_failure_output(output: str, max_lines: int = 24, max_chars: int = 1800) -> str:
    lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        return ""
    selected = lines[: max_lines // 2] + (["..."] if len(lines) > max_lines else []) + lines[-max_lines // 2 :]
    text = "\n".join(selected)
    return text[:max_chars]


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        return default
    return default
