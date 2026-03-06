from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.planning import ActionClass, ChangeImpact, compact_failure_output
from villani_code.project_memory import ValidationConfig, ValidationStep, load_repo_map, load_validation_config


@dataclass(slots=True)
class ValidationTarget:
    path: str
    target_type: str
    confidence: float


@dataclass(slots=True)
class ValidationSelectionReason:
    step_name: str
    reason: str


@dataclass(slots=True)
class ValidationScope:
    changed_files: list[str]
    docs_only: bool
    formatting_only: bool
    manifests_changed: bool
    config_changed: bool
    dependency_changed: bool
    test_files_changed: list[str]
    source_files_changed: list[str]


@dataclass(slots=True)
class ValidationEscalationPolicy:
    broaden_after_targeted_pass: bool
    force_broad: bool
    reason: str


@dataclass(slots=True)
class ValidationPlanStep:
    step: ValidationStep
    command: str
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationPlan:
    scope: ValidationScope
    selected_steps: list[ValidationPlanStep]
    reasons: list[ValidationSelectionReason]
    targets: list[ValidationTarget]
    escalation: ValidationEscalationPolicy


@dataclass(slots=True)
class ValidationFailureSummary:
    step_name: str
    failure_class: str
    headline: str
    relevant_paths: list[str]
    relevant_error_lines: list[str]
    concise_summary: str
    recommended_repair_scope: str
    compact_output: str


@dataclass(slots=True)
class ValidationStepResult:
    step: ValidationStep
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(slots=True)
class ValidationRunSummary:
    passed: bool
    executed_steps: list[str]
    escalation_applied: bool


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    plan: ValidationPlan
    steps: list[ValidationStepResult] = field(default_factory=list)
    failure_summary: str = ""
    structured_failure: ValidationFailureSummary | None = None
    run_summary: ValidationRunSummary | None = None


def _normalize(files: list[str]) -> list[str]:
    return [f.replace("\\", "/").lstrip("./") for f in files]


def infer_validation_scope(changed_files: list[str]) -> ValidationScope:
    files = _normalize(changed_files)
    manifests = {"pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod"}
    lockfiles = {"poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
    configs = {"ruff.toml", ".ruff.toml", "mypy.ini", "pytest.ini", "tox.ini", "setup.cfg", "tsconfig.json"}

    test_files = [f for f in files if f.startswith("tests/") or f.endswith("_test.py") or ".test." in f or ".spec." in f]
    source_files = [f for f in files if f.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go")) and f not in test_files]
    docs_only = bool(files) and all(f.startswith("docs/") or Path(f).suffix.lower() in {".md", ".rst", ".txt"} for f in files)
    formatting_only = bool(files) and all(Path(f).suffix.lower() in {".md", ".rst", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".toml", ".yaml", ".yml"} for f in files)
    manifests_changed = any(Path(f).name in manifests for f in files)
    dependency_changed = manifests_changed or any(Path(f).name in lockfiles for f in files)
    config_changed = dependency_changed or any(Path(f).name in configs for f in files)

    return ValidationScope(files, docs_only, formatting_only, manifests_changed, config_changed, dependency_changed, test_files, source_files)


def _related_test_candidates(src_file: str, test_roots: list[str]) -> list[str]:
    path = Path(src_file)
    module = path.stem
    module_parent = "/".join(path.parts[1:-1]) if len(path.parts) > 2 and path.parts[0] == "src" else "/".join(path.parts[:-1])
    roots = test_roots or ["tests"]
    out: list[str] = []
    for root in roots:
        out.extend([
            f"{root}/test_{module}.py",
            f"{root}/{module}_test.py",
        ])
        if module_parent and module_parent not in {".", ""}:
            out.append(f"{root}/{module_parent}/test_{module}.py")
    return out


def infer_validation_targets(changed_files: list[str], repo_map: dict[str, Any] | None = None) -> list[ValidationTarget]:
    repo_map = repo_map or {}
    scope = infer_validation_scope(changed_files)
    targets: list[ValidationTarget] = []
    if scope.test_files_changed:
        for file in scope.test_files_changed[:6]:
            targets.append(ValidationTarget(path=file, target_type="test_file", confidence=0.95))
        return targets
    for src in scope.source_files_changed[:6]:
        for candidate in _related_test_candidates(src, repo_map.get("test_roots", []))[:3]:
            targets.append(ValidationTarget(path=candidate, target_type="related_test", confidence=0.65))
    return targets


def infer_targeted_command(step: ValidationStep, changed_files: list[str], repo_map: dict[str, Any] | None = None) -> str:
    if step.kind != "test" or "pytest" not in step.command:
        return step.command
    targets = infer_validation_targets(changed_files, repo_map)
    if not targets:
        return step.command
    quoted = " ".join(shlex.quote(t.path) for t in targets[:6])
    return f"python -m pytest -q {quoted}".strip()


def _step_order(step: ValidationStep) -> tuple[int, int, str]:
    kind_order = {"format": 0, "lint": 1, "typecheck": 2, "test": 3, "build": 4, "inspection": 5}
    return (step.cost_level, kind_order.get(step.kind, 9), step.name)


def _impact_from_inputs(scope: ValidationScope, change_impact: str | None, action_classes: list[str] | None) -> ChangeImpact:
    actions = set(action_classes or [])
    if scope.dependency_changed or ActionClass.DEPENDENCY_CHANGE.value in actions:
        return ChangeImpact.DEPENDENCY_SURFACE
    if scope.manifests_changed or scope.config_changed or ActionClass.CONFIG_EDIT.value in actions:
        return ChangeImpact.CONFIG_ONLY
    if change_impact:
        try:
            return ChangeImpact(change_impact)
        except ValueError:
            pass
    if scope.test_files_changed and not scope.source_files_changed:
        return ChangeImpact.TESTS_ONLY
    if scope.test_files_changed and scope.source_files_changed:
        return ChangeImpact.SOURCE_AND_TESTS
    return ChangeImpact.SOURCE_ONLY


def plan_validation(config: ValidationConfig, changed_files: list[str], repo_map: dict[str, Any] | None = None, change_impact: str | None = None, action_classes: list[str] | None = None) -> ValidationPlan:
    scope = infer_validation_scope(changed_files)
    enabled = sorted([s for s in config.steps if s.enabled], key=_step_order)
    impact = _impact_from_inputs(scope, change_impact, action_classes)
    targets = infer_validation_targets(changed_files, repo_map)

    selected: list[ValidationPlanStep] = []
    reasons: list[ValidationSelectionReason] = []

    def include(step: ValidationStep, reason: str) -> None:
        if step.name in {s.step.name for s in selected}:
            return
        command = infer_targeted_command(step, scope.changed_files, repo_map)
        selected.append(ValidationPlanStep(step=step, command=command, reasons=[reason]))
        reasons.append(ValidationSelectionReason(step_name=step.name, reason=reason))

    if not scope.changed_files:
        for step in enabled:
            if step.kind in {"format", "lint", "inspection"}:
                include(step, "No changed files: run only cheap sanity checks.")
        return ValidationPlan(scope, selected, reasons, targets, ValidationEscalationPolicy(False, False, "no_changes"))

    if scope.docs_only:
        for step in enabled:
            if step.kind in {"format", "lint", "inspection"}:
                include(step, "Docs-only changes: skip code-heavy checks.")
        return ValidationPlan(scope, selected, reasons, targets, ValidationEscalationPolicy(False, False, "docs_only"))

    force_broad = impact in {ChangeImpact.CONFIG_ONLY, ChangeImpact.DEPENDENCY_SURFACE, ChangeImpact.PACKAGE_WIDE_BEHAVIOR, ChangeImpact.REPO_WIDE_BEHAVIOR} or scope.dependency_changed

    for step in enabled:
        if step.kind in {"format", "lint"}:
            include(step, "Low-cost static checks run first.")

    for step in enabled:
        if step.kind == "typecheck" and (scope.source_files_changed or force_broad):
            include(step, "Type checks relevant for source/config impact.")

    if targets:
        for step in enabled:
            if step.kind == "test" and step.scope_hint in {"targeted", "project", "repo"}:
                include(step, "Targeted tests inferred from changed files.")
                break

    if force_broad or not targets:
        for step in enabled:
            if step.kind == "test":
                include(step, "Broad tests required by impact or uncertain mapping.")

    for step in enabled:
        if step.kind in {"build", "inspection"}:
            include(step, "Final safety checks.")

    escalation = ValidationEscalationPolicy(
        broaden_after_targeted_pass=bool(targets) and not force_broad,
        force_broad=force_broad,
        reason="config_or_dependency_change" if force_broad else "targeted_then_broaden",
    )
    return ValidationPlan(scope, selected, reasons, targets, escalation)


def summarize_validation_failure(step_name: str, stdout: str, stderr: str) -> ValidationFailureSummary:
    combined = (stdout + "\n" + stderr).strip()
    compact = compact_failure_output(combined)
    lines = [ln.strip() for ln in compact.splitlines() if ln.strip()]
    error_lines = [ln for ln in lines if any(tok in ln.lower() for tok in ["error", "failed", "traceback", "exception"])][:6]
    paths = [tok for ln in lines for tok in ln.split() if "/" in tok and "." in tok][:6]
    failure_class = "test_failure" if "pytest" in step_name else "command_failure"
    concise = error_lines[0] if error_lines else (lines[0] if lines else f"{step_name} failed")
    return ValidationFailureSummary(
        step_name=step_name,
        failure_class=failure_class,
        headline=f"{step_name} failed",
        relevant_paths=paths,
        relevant_error_lines=error_lines,
        concise_summary=concise,
        recommended_repair_scope="targeted" if "target" in step_name else "step_scope",
        compact_output=compact,
    )


def run_validation(repo: Path, changed_files: list[str], event_callback: Any | None = None, steps_override: list[str] | None = None, repo_map: dict[str, Any] | None = None, change_impact: str | None = None, action_classes: list[str] | None = None) -> ValidationResult:
    cfg = load_validation_config(repo)
    repo_map = repo_map or load_repo_map(repo)
    plan = plan_validation(cfg, changed_files, repo_map=repo_map, change_impact=change_impact, action_classes=action_classes)

    if steps_override:
        allowed = set(steps_override)
        plan.selected_steps = [s for s in plan.selected_steps if s.step.name in allowed]

    results: list[ValidationStepResult] = []
    for planned_step in plan.selected_steps:
        step = planned_step.step
        command = planned_step.command
        if event_callback:
            event_callback({"type": "validation_step_started", "name": step.name, "command": command, "reasons": planned_step.reasons})
        started = time.monotonic()
        proc = subprocess.run(command, shell=True, cwd=repo, text=True, capture_output=True)
        result = ValidationStepResult(step, command, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - started, planned_step.reasons)
        results.append(result)
        if event_callback:
            event_callback({"type": "validation_step_finished", "name": step.name, "exit_code": proc.returncode, "duration": result.duration_seconds})
        if proc.returncode != 0:
            failure = summarize_validation_failure(step.name, proc.stdout, proc.stderr)
            if event_callback:
                event_callback({"type": "validation_step_failed", "name": step.name, "summary": failure.concise_summary})
            return ValidationResult(False, plan, results, f"{failure.headline}\n{failure.compact_output}", failure, ValidationRunSummary(False, [s.step.name for s in results], False))

    return ValidationResult(True, plan, results, run_summary=ValidationRunSummary(True, [s.step.name for s in results], False))


def select_validation_steps(config: ValidationConfig, changed_files: list[str]) -> list[ValidationStep]:
    return [row.step for row in plan_validation(config, changed_files).selected_steps]
