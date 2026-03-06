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


class ActionClass(str, Enum):
    READ_ONLY = "read_only"
    CODE_EDIT = "code_edit"
    CONFIG_EDIT = "config_edit"
    DEPENDENCY_CHANGE = "dependency_change"
    MIGRATION = "migration"
    TEST_REPAIR = "test_repair"
    BROAD_REFACTOR = "broad_refactor"
    SHELL_READ_ONLY = "shell_read_only"
    SHELL_MUTATING = "shell_mutating"
    DESTRUCTIVE_OPERATION = "destructive_operation"


class EstimatedScope(str, Enum):
    SINGLE_FILE = "single_file"
    NARROW_MULTI_FILE = "narrow_multi_file"
    BROAD_MULTI_FILE = "broad_multi_file"
    REPO_WIDE = "repo_wide"


@dataclass(slots=True)
class PlanAnalysis:
    candidate_targets: list[str] = field(default_factory=list)
    action_classes: list[ActionClass] = field(default_factory=list)
    estimated_scope: EstimatedScope = EstimatedScope.SINGLE_FILE
    confidence_score: float = 0.5
    rationale: list[str] = field(default_factory=list)

    # backwards compatibility with existing unit tests that construct PlanAnalysis manually
    touches_multiple_files: bool = False
    dependency_change: bool = False
    migration_like: bool = False
    refactor_like: bool = False
    test_fix_requires_edits: bool = False
    destructive_shell: bool = False


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
    action_classes: list[str] = field(default_factory=list)
    estimated_scope: str = EstimatedScope.SINGLE_FILE.value
    rationale: list[str] = field(default_factory=list)
    confidence_score: float = 0.5
    requires_write_phase: bool = False
    requires_validation_phase: bool = False

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
            f"- estimated_scope: {self.estimated_scope}",
            f"- confidence_score: {self.confidence_score:.2f}",
            f"- action_classes: {', '.join(self.action_classes) if self.action_classes else 'none'}",
            f"- assumptions: {', '.join(self.assumptions) if self.assumptions else 'none'}",
            f"- relevant_files: {', '.join(self.relevant_files) if self.relevant_files else 'none'}",
            "- rationale:",
        ]
        parts.extend(f"  - {r}" for r in self.rationale[:8])
        parts.append("- proposed_actions:")
        parts.extend(f"  - {a}" for a in self.proposed_actions)
        parts.append("- risks:")
        parts.extend(f"  - {r}" for r in self.risks)
        parts.append("- validation_steps:")
        parts.extend(f"  - {v}" for v in self.validation_steps)
        parts.append("- done_criteria:")
        parts.extend(f"  - {d}" for d in self.done_criteria)
        return "\n".join(parts)


def _tokenize_instruction(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./-]+", text.lower()))


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _scope_from_targets(targets: list[str]) -> EstimatedScope:
    if not targets:
        return EstimatedScope.SINGLE_FILE
    if len(targets) == 1:
        return EstimatedScope.SINGLE_FILE
    dirs = {t.split("/")[0] for t in targets if "/" in t}
    if len(targets) <= 3 and len(dirs) <= 2:
        return EstimatedScope.NARROW_MULTI_FILE
    if len(targets) <= 8:
        return EstimatedScope.BROAD_MULTI_FILE
    return EstimatedScope.REPO_WIDE


def analyze_instruction(
    instruction: str,
    repo_map: dict[str, Any] | None,
    validation_steps: list[str] | None,
) -> PlanAnalysis:
    text = instruction.strip().lower()
    tokens = _tokenize_instruction(text)
    repo_map = repo_map or {}

    manifests = [_normalize_path(p) for p in repo_map.get("manifests", [])]
    config_files = [_normalize_path(p) for p in repo_map.get("config_files", [])]
    source_roots = [_normalize_path(p) for p in repo_map.get("source_roots", [])]
    test_roots = [_normalize_path(p) for p in repo_map.get("test_roots", [])]

    candidate_targets: list[str] = []
    rationale: list[str] = []

    for path in manifests + config_files:
        filename = path.split("/")[-1].lower()
        stem = filename.split(".")[0]
        if filename in text or stem in tokens:
            candidate_targets.append(path)
            rationale.append(f"Instruction explicitly references {path}.")

    for root in source_roots + test_roots:
        if root.lower() in tokens or f"{root}/" in text:
            candidate_targets.append(root)
            rationale.append(f"Instruction references root '{root}'.")

    if "docs" in tokens or "readme" in tokens:
        candidate_targets.extend([p for p in repo_map.get("docs_roots", [])][:2])
        rationale.append("Documentation terms detected in instruction.")

    action_classes: set[ActionClass] = set()
    destructive_tokens = {"rm", "rm -rf", "reset", "rebase", "force-push", "drop", "delete"}
    if any(token in text for token in destructive_tokens):
        action_classes.add(ActionClass.DESTRUCTIVE_OPERATION)
        rationale.append("Destructive shell/git operation terms detected.")

    if {"inspect", "review", "analyze", "summarize"} & tokens:
        action_classes.add(ActionClass.READ_ONLY)
    if {"bash", "shell", "command"} & tokens:
        action_classes.add(ActionClass.SHELL_READ_ONLY)
    if {"edit", "change", "implement", "fix", "patch", "refactor"} & tokens:
        action_classes.add(ActionClass.CODE_EDIT)
    if {"config", "setting", "settings", "ini", "toml", "yaml", "json"} & tokens:
        action_classes.add(ActionClass.CONFIG_EDIT)
    if {"dependency", "dependencies", "requirements", "lockfile", "pyproject", "package.json"} & tokens:
        action_classes.add(ActionClass.DEPENDENCY_CHANGE)
        candidate_targets.extend(manifests)
        rationale.append("Dependency-related instruction terms align with repository manifests.")
    if {"migrate", "migration", "schema", "alembic", "database"} & tokens:
        action_classes.add(ActionClass.MIGRATION)
    if {"test", "tests", "pytest", "failing"} & tokens and {"fix", "repair", "pass"} & tokens:
        action_classes.add(ActionClass.TEST_REPAIR)
    if {"refactor", "rename", "restructure"} & tokens:
        action_classes.add(ActionClass.BROAD_REFACTOR)

    if not action_classes:
        action_classes.add(ActionClass.READ_ONLY)

    if ActionClass.READ_ONLY in action_classes and len(action_classes) > 1:
        action_classes.discard(ActionClass.READ_ONLY)

    targets = sorted(dict.fromkeys([t for t in candidate_targets if t]))
    if not targets:
        targets = sorted(dict.fromkeys((manifests + source_roots + test_roots + config_files)[:6]))
        rationale.append("No explicit path mention; using high-signal roots/manifests from repo map.")

    estimated_scope = _scope_from_targets(targets)
    if ActionClass.BROAD_REFACTOR in action_classes:
        estimated_scope = max(estimated_scope, EstimatedScope.BROAD_MULTI_FILE, key=lambda s: list(EstimatedScope).index(s))
    if ActionClass.DEPENDENCY_CHANGE in action_classes or ActionClass.MIGRATION in action_classes:
        estimated_scope = max(estimated_scope, EstimatedScope.NARROW_MULTI_FILE, key=lambda s: list(EstimatedScope).index(s))

    confidence = 0.4
    if targets:
        confidence += 0.2
    if repo_map:
        confidence += 0.15
    if validation_steps:
        confidence += 0.1
    if len(action_classes) <= 3:
        confidence += 0.1
    if any(r.startswith("Instruction explicitly references") for r in rationale):
        confidence += 0.1

    confidence = max(0.1, min(1.0, confidence))

    return PlanAnalysis(
        candidate_targets=targets,
        action_classes=sorted(action_classes, key=lambda a: a.value),
        estimated_scope=estimated_scope,
        confidence_score=confidence,
        rationale=rationale,
        touches_multiple_files=estimated_scope is not EstimatedScope.SINGLE_FILE,
        dependency_change=ActionClass.DEPENDENCY_CHANGE in action_classes,
        migration_like=ActionClass.MIGRATION in action_classes,
        refactor_like=ActionClass.BROAD_REFACTOR in action_classes,
        test_fix_requires_edits=ActionClass.TEST_REPAIR in action_classes,
        destructive_shell=ActionClass.DESTRUCTIVE_OPERATION in action_classes,
    )


def classify_plan_risk(instruction: str, analysis: PlanAnalysis) -> PlanRiskLevel:
    action_set = set(analysis.action_classes)

    if analysis.destructive_shell:
        action_set.add(ActionClass.DESTRUCTIVE_OPERATION)
    if analysis.dependency_change:
        action_set.add(ActionClass.DEPENDENCY_CHANGE)
    if analysis.migration_like:
        action_set.add(ActionClass.MIGRATION)
    if analysis.refactor_like:
        action_set.add(ActionClass.BROAD_REFACTOR)
    if analysis.test_fix_requires_edits:
        action_set.add(ActionClass.TEST_REPAIR)

    if ActionClass.DESTRUCTIVE_OPERATION in action_set:
        return PlanRiskLevel.HIGH
    if ActionClass.DEPENDENCY_CHANGE in action_set or ActionClass.MIGRATION in action_set:
        return PlanRiskLevel.HIGH
    if ActionClass.BROAD_REFACTOR in action_set and analysis.estimated_scope in {
        EstimatedScope.BROAD_MULTI_FILE,
        EstimatedScope.REPO_WIDE,
    }:
        return PlanRiskLevel.HIGH

    if analysis.touches_multiple_files or analysis.estimated_scope in {EstimatedScope.NARROW_MULTI_FILE, EstimatedScope.BROAD_MULTI_FILE, EstimatedScope.REPO_WIDE}:
        return PlanRiskLevel.MEDIUM
    if action_set & {ActionClass.CONFIG_EDIT, ActionClass.TEST_REPAIR, ActionClass.SHELL_MUTATING, ActionClass.SHELL_READ_ONLY, ActionClass.CODE_EDIT, ActionClass.BROAD_REFACTOR}:
        return PlanRiskLevel.MEDIUM
    return PlanRiskLevel.LOW


def is_non_trivial_task(_instruction: str, analysis: PlanAnalysis) -> bool:
    return classify_plan_risk(_instruction, analysis) != PlanRiskLevel.LOW or bool(set(analysis.action_classes) - {ActionClass.READ_ONLY})


def generate_execution_plan(
    instruction: str,
    repo: Path,
    repo_map: dict[str, Any] | None,
    validation_steps: list[str] | None,
) -> ExecutionPlan:
    text = instruction.strip()
    analysis = analyze_instruction(text, repo_map, validation_steps)
    risk = classify_plan_risk(text, analysis)
    non_trivial = is_non_trivial_task(text, analysis)

    actions = ["Inspect candidate targets and repository constraints before mutating files."]
    requires_write = bool(set(analysis.action_classes) - {ActionClass.READ_ONLY, ActionClass.SHELL_READ_ONLY})
    requires_validation = requires_write or ActionClass.TEST_REPAIR in set(analysis.action_classes)
    if requires_write:
        actions.append("Apply minimal deterministic edits scoped to inferred targets.")
    if requires_validation:
        actions.append("Execute validation plan in relevance/cost order; stop on first failing gate.")
    if not requires_write:
        actions.append("Summarize findings without mutating repository files.")

    risks = [
        "Potential regressions in changed modules.",
    ]
    if risk is PlanRiskLevel.HIGH:
        risks.append("High-impact operation inferred from dependency/migration/destructive evidence.")
    elif risk is PlanRiskLevel.MEDIUM:
        risks.append("Cross-file or config/test-sensitive changes require careful validation.")
    else:
        risks.append("Narrow scope with no structural risk signals.")

    validation = validation_steps or ["validation-configured-steps"]

    assumptions = [
        "Repository has a valid working tree and accessible toolchain.",
        "Detected repo map reflects current checkout structure.",
    ]
    done = [
        "Task goal satisfied with deterministic edits.",
        "Validation gates pass or unresolved failures are explicitly summarized.",
        "Changed files and rationale are reported.",
    ]

    return ExecutionPlan(
        task_goal=text,
        assumptions=assumptions,
        relevant_files=analysis.candidate_targets,
        proposed_actions=actions,
        risks=risks,
        validation_steps=validation,
        done_criteria=done,
        risk_level=risk,
        non_trivial=non_trivial,
        action_classes=[a.value for a in analysis.action_classes],
        estimated_scope=analysis.estimated_scope.value,
        rationale=analysis.rationale,
        confidence_score=analysis.confidence_score,
        requires_write_phase=requires_write,
        requires_validation_phase=requires_validation,
    )


def compact_failure_output(output: str, max_lines: int = 24, max_chars: int = 1800) -> str:
    lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) <= max_lines:
        text = "\n".join(lines)
        return text[:max_chars]
    head = max_lines // 2
    tail = max_lines - head - 1
    selected = lines[:head] + ["..."] + lines[-tail:]
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
