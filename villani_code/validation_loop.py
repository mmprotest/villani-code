from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from villani_code.planning import compact_failure_output
from villani_code.project_memory import ValidationConfig, ValidationStep, load_validation_config


@dataclass(slots=True)
class ValidationStepResult:
    step: ValidationStep
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    steps: list[ValidationStepResult] = field(default_factory=list)
    failure_summary: str = ""


@dataclass(slots=True)
class RepairAttemptSummary:
    attempt_number: int
    failing_step: str
    failure_summary: str
    repair_summary: str


def infer_targeted_command(step: ValidationStep, changed_files: list[str]) -> str:
    if step.kind != "test" or not changed_files:
        return step.command
    test_files = [f for f in changed_files if "/test" in f or f.startswith("tests/") or f.endswith("_test.py") or f.endswith("test.py")]
    if test_files and "pytest" in step.command:
        return f"python -m pytest -q {' '.join(test_files[:3])}"
    py_files = [f for f in changed_files if f.endswith(".py")]
    if py_files and "pytest" in step.command:
        pkg = py_files[0].split("/")[0]
        return f"python -m pytest -q {pkg}"
    return step.command


def select_validation_steps(config: ValidationConfig, changed_files: list[str]) -> list[ValidationStep]:
    enabled = [s for s in config.steps if s.enabled]
    enabled.sort(key=lambda s: (s.cost_level, s.kind))
    if not changed_files:
        return [s for s in enabled if s.kind in {"lint", "format", "inspection"}]
    if all(f.endswith((".md", ".txt", ".rst")) for f in changed_files):
        return [s for s in enabled if s.kind in {"lint", "format", "inspection"}]
    return enabled


def run_validation(repo: Path, changed_files: list[str], event_callback: Any | None = None) -> ValidationResult:
    cfg = load_validation_config(repo)
    steps = select_validation_steps(cfg, changed_files)
    results: list[ValidationStepResult] = []
    for step in steps:
        command = infer_targeted_command(step, changed_files)
        if event_callback:
            event_callback({"type": "validation_step_started", "name": step.name, "command": command})
        started = time.monotonic()
        proc = subprocess.run(command, shell=True, cwd=repo, text=True, capture_output=True)
        result = ValidationStepResult(step=step, exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, duration_seconds=time.monotonic() - started)
        results.append(result)
        if event_callback:
            event_callback({"type": "validation_step_finished", "name": step.name, "exit_code": proc.returncode})
        if proc.returncode != 0:
            combined = (proc.stdout + "\n" + proc.stderr).strip()
            return ValidationResult(passed=False, steps=results, failure_summary=f"{step.name} failed\n{compact_failure_output(combined)}")
    return ValidationResult(passed=True, steps=results)
