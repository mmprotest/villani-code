from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.planning import compact_failure_output
from villani_code.project_memory import ValidationConfig, ValidationStep, load_validation_config


@dataclass(slots=True)
class ValidationTarget:
    value: str
    target_type: str


@dataclass(slots=True)
class ValidationSelectionReason:
    step_name: str
    reason: str


@dataclass(slots=True)
class ValidationScope:
    changed_files: list[str]
    docs_only: bool
    config_changed: bool
    manifests_changed: bool
    test_files_changed: list[str]
    source_files_changed: list[str]


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


@dataclass(slots=True)
class ValidationFailureSummary:
    step_name: str
    headline: str
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
class ValidationResult:
    passed: bool
    plan: ValidationPlan
    steps: list[ValidationStepResult] = field(default_factory=list)
    failure_summary: str = ""
    structured_failure: ValidationFailureSummary | None = None


@dataclass(slots=True)
class RepairAttemptSummary:
    attempt_number: int
    failing_step: str
    failure_summary: str
    repair_summary: str


def _normalize(files: list[str]) -> list[str]:
    return [f.replace("\\", "/").lstrip("./") for f in files]


def infer_validation_scope(changed_files: list[str]) -> ValidationScope:
    files = _normalize(changed_files)
    docs_only = bool(files) and all(Path(f).suffix.lower() in {".md", ".rst", ".txt"} or f.startswith("docs/") for f in files)
    manifests_changed = any(Path(f).name in {"pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod", "poetry.lock", "Pipfile.lock"} for f in files)
    config_changed = manifests_changed or any(Path(f).name in {"ruff.toml", ".ruff.toml", "mypy.ini", "pytest.ini", "tox.ini", "setup.cfg", "tsconfig.json"} for f in files)
    test_files = [f for f in files if f.startswith("tests/") or f.endswith("_test.py") or f.endswith("test.py")]
    source_files = [f for f in files if f.endswith(".py") and f not in test_files]
    return ValidationScope(
        changed_files=files,
        docs_only=docs_only,
        config_changed=config_changed,
        manifests_changed=manifests_changed,
        test_files_changed=test_files,
        source_files_changed=source_files,
    )


def _python_related_tests(src_file: str) -> list[str]:
    path = Path(src_file)
    stem = path.stem
    parent = path.parent.as_posix()
    top = path.parts[0] if path.parts else ""
    candidates = [
        f"tests/test_{stem}.py",
        f"tests/{stem}_test.py",
    ]
    if parent and parent not in {".", ""}:
        if top in {"src", "villani_code"}:
            module_parent = "/".join(path.parts[1:-1])
            if module_parent:
                candidates.append(f"tests/{module_parent}/test_{stem}.py")
        else:
            candidates.append(f"tests/{parent}/test_{stem}.py")
    return candidates


def infer_targeted_command(step: ValidationStep, changed_files: list[str]) -> str:
    scope = infer_validation_scope(changed_files)
    if step.kind != "test" or not scope.changed_files or "pytest" not in step.command:
        return step.command

    targets: list[str] = []
    if scope.test_files_changed:
        targets.extend(scope.test_files_changed[:4])
    else:
        for src in scope.source_files_changed[:3]:
            targets.extend(_python_related_tests(src))

    if targets:
        deduped = []
        for item in targets:
            if item not in deduped:
                deduped.append(item)
        quoted = " ".join(shlex.quote(t) for t in deduped[:4])
        return f"python -m pytest -q {quoted}".strip()
    return step.command


def _step_order(step: ValidationStep) -> tuple[int, int, str]:
    kind_order = {"format": 0, "lint": 1, "typecheck": 2, "test": 3, "build": 4, "inspection": 5}
    return (step.cost_level, kind_order.get(step.kind, 9), step.name)


def select_validation_steps(config: ValidationConfig, changed_files: list[str]) -> list[ValidationStep]:
    plan = plan_validation(config, changed_files)
    return [row.step for row in plan.selected_steps]


def plan_validation(config: ValidationConfig, changed_files: list[str]) -> ValidationPlan:
    scope = infer_validation_scope(changed_files)
    enabled = sorted([s for s in config.steps if s.enabled], key=_step_order)

    selected: list[ValidationPlanStep] = []
    reasons: list[ValidationSelectionReason] = []

    def include(step: ValidationStep, reason: str) -> None:
        if step.name in {s.step.name for s in selected}:
            return
        command = infer_targeted_command(step, scope.changed_files)
        selected.append(ValidationPlanStep(step=step, command=command, reasons=[reason]))
        reasons.append(ValidationSelectionReason(step_name=step.name, reason=reason))

    if not scope.changed_files:
        for step in enabled:
            if step.kind in {"format", "lint", "inspection"}:
                include(step, "No changed files; run cheap hygiene checks only.")
        return ValidationPlan(scope=scope, selected_steps=selected, reasons=reasons)

    if scope.docs_only:
        for step in enabled:
            if step.kind in {"format", "lint", "inspection"} and step.language_family != "python":
                include(step, "Docs-only change; skipping code-heavy validation.")
        if not selected:
            for step in enabled:
                if step.kind in {"inspection", "format", "lint"}:
                    include(step, "Docs-only fallback checks.")
        return ValidationPlan(scope=scope, selected_steps=selected, reasons=reasons)

    if scope.config_changed:
        for step in enabled:
            include(step, "Config/manifest changed; escalate to full validation breadth.")
        return ValidationPlan(scope=scope, selected_steps=selected, reasons=reasons)

    if scope.test_files_changed:
        for step in enabled:
            if step.kind == "test" and step.scope_hint in {"targeted", "project", "repo"}:
                include(step, "Changed files include tests; prioritize targeted test execution.")
                break

    for step in enabled:
        if step.kind in {"format", "lint"}:
            include(step, "Run low-cost static checks before expensive validation.")

    for step in enabled:
        if step.kind == "typecheck":
            include(step, "Type checks are relevant for source edits.")

    for step in enabled:
        if step.kind == "test":
            include(step, "Run test validation for changed source files.")

    for step in enabled:
        if step.kind in {"build", "inspection"}:
            include(step, "Run remaining safety checks.")

    return ValidationPlan(scope=scope, selected_steps=selected, reasons=reasons)


def summarize_validation_failure(step_name: str, stdout: str, stderr: str) -> ValidationFailureSummary:
    combined = (stdout + "\n" + stderr).strip()
    compact = compact_failure_output(combined)
    headline = f"{step_name} failed"
    return ValidationFailureSummary(step_name=step_name, headline=headline, compact_output=compact)


def run_validation(repo: Path, changed_files: list[str], event_callback: Any | None = None, steps_override: list[str] | None = None) -> ValidationResult:
    cfg = load_validation_config(repo)
    plan = plan_validation(cfg, changed_files)

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
        result = ValidationStepResult(
            step=step,
            command=command,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_seconds=time.monotonic() - started,
            reasons=planned_step.reasons,
        )
        results.append(result)
        if event_callback:
            event_callback({"type": "validation_step_finished", "name": step.name, "exit_code": proc.returncode, "duration": result.duration_seconds})
        if proc.returncode != 0:
            failure = summarize_validation_failure(step.name, proc.stdout, proc.stderr)
            if event_callback:
                event_callback({"type": "validation_step_failed", "name": step.name, "summary": failure.compact_output})
            return ValidationResult(
                passed=False,
                plan=plan,
                steps=results,
                failure_summary=f"{failure.headline}\n{failure.compact_output}",
                structured_failure=failure,
            )

    return ValidationResult(passed=True, plan=plan, steps=results)
