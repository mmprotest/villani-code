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
    REFACTOR_NARROW = "refactor_narrow"
    REFACTOR_BROAD = "refactor_broad"
    SHELL_READ_ONLY = "shell_read_only"
    SHELL_MUTATING = "shell_mutating"
    GIT_SAFE = "git_safe"
    GIT_DESTRUCTIVE = "git_destructive"
    FILE_DELETE_OR_MOVE = "file_delete_or_move"


class EstimatedScope(str, Enum):
    SINGLE_FILE = "single_file"
    NARROW_MULTI_FILE = "narrow_multi_file"
    BROAD_MULTI_FILE = "broad_multi_file"
    PACKAGE_WIDE = "package_wide"
    REPO_WIDE = "repo_wide"


class ChangeImpact(str, Enum):
    TESTS_ONLY = "tests_only"
    SOURCE_ONLY = "source_only"
    SOURCE_AND_TESTS = "source_and_tests"
    CONFIG_ONLY = "config_only"
    DEPENDENCY_SURFACE = "dependency_surface"
    PACKAGE_WIDE_BEHAVIOR = "package_wide_behavior"
    REPO_WIDE_BEHAVIOR = "repo_wide_behavior"


@dataclass(slots=True)
class CandidateTarget:
    target: str
    target_type: str
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PlanningEvidence:
    matched_terms: list[str] = field(default_factory=list)
    matched_paths: list[str] = field(default_factory=list)
    source_roots: list[str] = field(default_factory=list)
    test_roots: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    configs: list[str] = field(default_factory=list)
    repo_shape: str = "unknown"
    explicit_signals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RiskAssessment:
    risk_level: PlanRiskLevel
    drivers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PlanAnalysis:
    candidate_targets: list[CandidateTarget] = field(default_factory=list)
    action_classes: list[ActionClass] = field(default_factory=list)
    estimated_scope: EstimatedScope = EstimatedScope.SINGLE_FILE
    change_impact: ChangeImpact = ChangeImpact.SOURCE_ONLY
    grounding_evidence: PlanningEvidence = field(default_factory=PlanningEvidence)
    confidence_score: float = 0.5
    rationale: list[str] = field(default_factory=list)
    requires_write_phase: bool = False
    requires_validation_phase: bool = False
    non_trivial: bool = False


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
    change_impact: str = ChangeImpact.SOURCE_ONLY.value
    candidate_targets: list[dict[str, Any]] = field(default_factory=list)
    grounding_evidence: dict[str, Any] = field(default_factory=dict)
    risk_assessment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_level"] = self.risk_level.value
        return payload

    def to_human_text(self) -> str:
        return "\n".join(
            [
                "Execution plan:",
                f"- task_goal: {self.task_goal}",
                f"- risk_level: {self.risk_level.value}",
                f"- risk_drivers: {', '.join(self.risk_assessment.get('drivers', [])) or 'none'}",
                f"- estimated_scope: {self.estimated_scope}",
                f"- change_impact: {self.change_impact}",
                f"- confidence_score: {self.confidence_score:.2f}",
                f"- action_classes: {', '.join(self.action_classes) if self.action_classes else 'none'}",
                f"- relevant_files: {', '.join(self.relevant_files) if self.relevant_files else 'none'}",
                "- rationale:",
                *[f"  - {r}" for r in self.rationale[:10]],
            ]
        )


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./-]+", text.lower()))


def _normalize(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _infer_action_classes(text: str, tokens: set[str]) -> set[ActionClass]:
    actions: set[ActionClass] = set()
    if {"inspect", "review", "explain", "summarize"} & tokens:
        actions.add(ActionClass.READ_ONLY)
    if {"fix", "edit", "implement", "patch", "change"} & tokens:
        actions.add(ActionClass.CODE_EDIT)
    if {"config", "setting", "yaml", "toml", "ini", "json"} & tokens:
        actions.add(ActionClass.CONFIG_EDIT)
    if {"dependency", "dependencies", "lockfile", "requirements", "pyproject", "package.json"} & tokens:
        actions.add(ActionClass.DEPENDENCY_CHANGE)
    if {"migration", "migrate", "schema", "alembic"} & tokens:
        actions.add(ActionClass.MIGRATION)
    if {"test", "pytest", "failing"} & tokens and {"fix", "repair", "pass"} & tokens:
        actions.add(ActionClass.TEST_REPAIR)
    if "refactor" in tokens:
        actions.add(ActionClass.REFACTOR_BROAD)
    if {"rename", "move", "delete", "remove"} & tokens:
        actions.add(ActionClass.FILE_DELETE_OR_MOVE)
    if {"bash", "shell", "command"} & tokens:
        actions.add(ActionClass.SHELL_READ_ONLY)
    if {"git", "commit", "status", "diff"} & tokens:
        actions.add(ActionClass.GIT_SAFE)
    if any(term in text for term in ["reset --hard", "rebase", "force-push", "rm -rf"]):
        actions.add(ActionClass.GIT_DESTRUCTIVE)
    if not actions:
        actions.add(ActionClass.READ_ONLY)
    return actions


def _scope(targets: list[str], actions: set[ActionClass], repo_shape: str) -> EstimatedScope:
    if ActionClass.REFACTOR_BROAD in actions or ActionClass.GIT_DESTRUCTIVE in actions:
        return EstimatedScope.REPO_WIDE
    if ActionClass.MIGRATION in actions:
        return EstimatedScope.PACKAGE_WIDE
    if len(targets) <= 1:
        return EstimatedScope.SINGLE_FILE
    if len(targets) <= 4:
        return EstimatedScope.NARROW_MULTI_FILE
    if repo_shape == "multi_root":
        return EstimatedScope.PACKAGE_WIDE
    return EstimatedScope.BROAD_MULTI_FILE


def _infer_change_impact(actions: set[ActionClass], targets: list[CandidateTarget], repo_map: dict[str, Any]) -> ChangeImpact:
    types = {t.target_type for t in targets}
    source_roots = set(repo_map.get("source_roots", []))
    if ActionClass.DEPENDENCY_CHANGE in actions or "lockfile" in types:
        return ChangeImpact.DEPENDENCY_SURFACE
    if "manifest" in types or "config" in types:
        return ChangeImpact.CONFIG_ONLY
    if "test" in types and "source" not in types:
        return ChangeImpact.TESTS_ONLY
    if "source" in types and "test" in types:
        return ChangeImpact.SOURCE_AND_TESTS
    roots_touched = {t.target.split("/")[0] for t in targets if "/" in t.target}
    if len(roots_touched & source_roots) > 1:
        return ChangeImpact.PACKAGE_WIDE_BEHAVIOR
    if ActionClass.REFACTOR_BROAD in actions or ActionClass.MIGRATION in actions:
        return ChangeImpact.REPO_WIDE_BEHAVIOR
    return ChangeImpact.SOURCE_ONLY


def analyze_instruction(instruction: str, repo_map: dict[str, Any] | None, validation_steps: list[str] | None) -> PlanAnalysis:
    text = instruction.strip().lower()
    tokens = _tokenize(text)
    repo_map = repo_map or {}
    manifests = [_normalize(v) for v in repo_map.get("manifests", []) + repo_map.get("lockfiles", [])]
    configs = [_normalize(v) for v in repo_map.get("config_files", [])]
    source_roots = [_normalize(v) for v in repo_map.get("source_roots", [])]
    test_roots = [_normalize(v) for v in repo_map.get("test_roots", [])]
    docs_roots = [_normalize(v) for v in repo_map.get("docs_roots", [])]

    candidates: dict[str, CandidateTarget] = {}

    def add_candidate(path: str, target_type: str, reason: str) -> None:
        key = _normalize(path)
        row = candidates.get(key)
        if row is None:
            row = CandidateTarget(target=key, target_type=target_type, evidence=[])
            candidates[key] = row
        if reason not in row.evidence:
            row.evidence.append(reason)

    for path in manifests:
        name = Path(path).name.lower()
        if name in text or name.split(".")[0] in tokens:
            target_type = "lockfile" if name in {"poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"} else "manifest"
            add_candidate(path, target_type, f"instruction mentions {name}")
    for path in configs:
        name = Path(path).name.lower()
        if name in text:
            add_candidate(path, "config", f"instruction mentions {name}")

    for root in source_roots:
        if root in tokens or root in text:
            add_candidate(root, "source", f"matches source root {root}")
    for root in test_roots:
        if root in tokens or root in text or "test" in tokens:
            add_candidate(root, "test", f"matches test root {root}")
    for root in docs_roots:
        if "docs" in tokens or "readme" in tokens:
            add_candidate(root, "docs", f"docs signal mapped to {root}")

    searchable_paths = [
        *[_normalize(v) for v in repo_map.get("package_roots", [])],
        *[_normalize(v) for v in repo_map.get("likely_entrypoints", [])],
        *source_roots,
        *test_roots,
    ]
    for path in sorted(set(searchable_paths)):
        name = Path(path).name.lower()
        stem = Path(path).stem.lower()
        if name in tokens or stem in tokens:
            target_type = "source" if path in source_roots else "test" if path in test_roots else "module"
            add_candidate(path, target_type, f"token-to-path grounding: {name}")

    if not candidates:
        for path in (source_roots + test_roots + manifests + configs)[:8]:
            ttype = "source" if path in source_roots else "test" if path in test_roots else "manifest" if path in manifests else "config"
            add_candidate(path, ttype, "fallback to high-signal repo map roots")

    action_classes = _infer_action_classes(text, tokens)
    scope = _scope(sorted(candidates), action_classes, str(repo_map.get("repo_shape", "single_package")))
    impact = _infer_change_impact(action_classes, list(candidates.values()), repo_map)

    confidence = 0.4
    if candidates:
        confidence += 0.2
    if repo_map:
        confidence += 0.2
    if validation_steps:
        confidence += 0.1
    if any("instruction mentions" in ev for c in candidates.values() for ev in c.evidence):
        confidence += 0.1

    evidence = PlanningEvidence(
        matched_terms=sorted(tokens)[:40],
        matched_paths=sorted(candidates)[:20],
        source_roots=source_roots[:8],
        test_roots=test_roots[:8],
        manifests=manifests[:8],
        configs=configs[:8],
        repo_shape=str(repo_map.get("repo_shape", "unknown")),
        explicit_signals=[ev for c in candidates.values() for ev in c.evidence][:12],
    )
    rationale = [
        f"Repo shape inferred as {evidence.repo_shape}.",
        f"Mapped {len(candidates)} candidate targets from instruction + repo map.",
        f"Predicted change impact: {impact.value}.",
    ]
    requires_write = bool(action_classes - {ActionClass.READ_ONLY, ActionClass.SHELL_READ_ONLY, ActionClass.GIT_SAFE})
    requires_validation = requires_write or ActionClass.TEST_REPAIR in action_classes
    return PlanAnalysis(
        candidate_targets=sorted(candidates.values(), key=lambda c: c.target),
        action_classes=sorted(action_classes, key=lambda a: a.value),
        estimated_scope=scope,
        change_impact=impact,
        grounding_evidence=evidence,
        confidence_score=max(0.1, min(1.0, confidence)),
        rationale=rationale,
        requires_write_phase=requires_write,
        requires_validation_phase=requires_validation,
        non_trivial=requires_write or scope != EstimatedScope.SINGLE_FILE,
    )


def classify_plan_risk(instruction: str, analysis: PlanAnalysis) -> PlanRiskLevel:
    _ = instruction
    actions = set(analysis.action_classes)
    if actions & {ActionClass.DEPENDENCY_CHANGE, ActionClass.MIGRATION, ActionClass.GIT_DESTRUCTIVE, ActionClass.FILE_DELETE_OR_MOVE}:
        return PlanRiskLevel.HIGH
    if analysis.estimated_scope in {EstimatedScope.REPO_WIDE, EstimatedScope.PACKAGE_WIDE}:
        return PlanRiskLevel.HIGH
    if analysis.change_impact in {ChangeImpact.CONFIG_ONLY, ChangeImpact.DEPENDENCY_SURFACE, ChangeImpact.PACKAGE_WIDE_BEHAVIOR, ChangeImpact.REPO_WIDE_BEHAVIOR}:
        return PlanRiskLevel.HIGH
    if analysis.estimated_scope in {EstimatedScope.NARROW_MULTI_FILE, EstimatedScope.BROAD_MULTI_FILE}:
        return PlanRiskLevel.MEDIUM
    if actions & {ActionClass.CODE_EDIT, ActionClass.CONFIG_EDIT, ActionClass.TEST_REPAIR, ActionClass.SHELL_MUTATING, ActionClass.REFACTOR_NARROW, ActionClass.REFACTOR_BROAD}:
        return PlanRiskLevel.MEDIUM
    return PlanRiskLevel.LOW


def _risk_assessment(analysis: PlanAnalysis, risk: PlanRiskLevel) -> RiskAssessment:
    drivers: list[str] = []
    if analysis.change_impact != ChangeImpact.SOURCE_ONLY:
        drivers.append(f"change_impact={analysis.change_impact.value}")
    if analysis.estimated_scope != EstimatedScope.SINGLE_FILE:
        drivers.append(f"scope={analysis.estimated_scope.value}")
    if any(a in {ActionClass.DEPENDENCY_CHANGE, ActionClass.MIGRATION, ActionClass.GIT_DESTRUCTIVE} for a in analysis.action_classes):
        drivers.append("high_impact_action_class_detected")
    if not drivers:
        drivers.append("narrow_read_or_edit_scope")
    return RiskAssessment(risk_level=risk, drivers=drivers)


def is_non_trivial_task(_instruction: str, analysis: PlanAnalysis) -> bool:
    return analysis.non_trivial


def generate_execution_plan(instruction: str, repo: Path, repo_map: dict[str, Any] | None, validation_steps: list[str] | None) -> ExecutionPlan:
    _ = repo
    text = instruction.strip()
    analysis = analyze_instruction(text, repo_map, validation_steps)
    risk = classify_plan_risk(text, analysis)
    risk_assessment = _risk_assessment(analysis, risk)

    return ExecutionPlan(
        task_goal=text,
        assumptions=["Repository structure is represented by .villani/repo_map.json.", "Validation step list in .villani/validation.json is current."],
        relevant_files=[c.target for c in analysis.candidate_targets],
        proposed_actions=[
            "Read high-signal candidate targets first.",
            "Apply minimal edits constrained to predicted artifact touch set." if analysis.requires_write_phase else "Keep repository read-only.",
            "Run validation in targeted-first then broadened order." if analysis.requires_validation_phase else "Skip validation (read-only task).",
        ],
        risks=[f"Risk classified as {risk.value}.", *risk_assessment.drivers],
        validation_steps=validation_steps or ["validation-configured-steps"],
        done_criteria=["Task outcome matches instruction.", "Validation outcomes are explicit.", "Checkpoint state contains compact handoff hints."],
        risk_level=risk,
        non_trivial=analysis.non_trivial,
        action_classes=[a.value for a in analysis.action_classes],
        estimated_scope=analysis.estimated_scope.value,
        rationale=analysis.rationale,
        confidence_score=analysis.confidence_score,
        requires_write_phase=analysis.requires_write_phase,
        requires_validation_phase=analysis.requires_validation_phase,
        change_impact=analysis.change_impact.value,
        candidate_targets=[asdict(c) for c in analysis.candidate_targets],
        grounding_evidence=asdict(analysis.grounding_evidence),
        risk_assessment={"risk_level": risk_assessment.risk_level.value, "drivers": risk_assessment.drivers},
    )


def compact_failure_output(output: str, max_lines: int = 24, max_chars: int = 1800) -> str:
    lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) <= max_lines:
        return "\n".join(lines)[:max_chars]
    head = max_lines // 2
    tail = max_lines - head - 1
    return "\n".join(lines[:head] + ["..."] + lines[-tail:])[:max_chars]


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
    return data if isinstance(data, dict) else default
