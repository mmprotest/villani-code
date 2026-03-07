from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from villani_code.benchmark.adapters.base import ValidationResult
from villani_code.benchmark.command_resolution import normalize_command_for_platform
from villani_code.benchmark.models import BenchmarkTask, ValidationCheckType


def execute_validation_checks(
    task: BenchmarkTask,
    repo_root: Path,
    on_check_start: Callable[[int, object], None] | None = None,
    on_check_end: Callable[[int, object, ValidationResult], None] | None = None,
) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    for index, check in enumerate(task.validation_checks):
        if on_check_start:
            on_check_start(index, check)
        if check.type == ValidationCheckType.COMMAND:
            cwd = repo_root if check.cwd is None else (repo_root / check.cwd)
            resolved = normalize_command_for_platform(check.command or "")
            if not resolved.argv:
                result = ValidationResult(
                    check_type=check.type.value,
                    success=False,
                    details="Validation command was empty.",
                    check_index=index,
                    failure_provenance="harness_failure",
                )
                results.append(result)
                if on_check_end:
                    on_check_end(index, check, result)
                continue
            try:
                proc = subprocess.run(
                    resolved.argv,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=check.timeout_seconds,
                )
                success = proc.returncode == check.expect_exit_code
                if success:
                    provenance = None
                elif proc.returncode == 124:
                    provenance = "timeout"
                else:
                    provenance = "validation_failure"
                details = (
                    f"command={resolved.display_command}\nexpected={check.expect_exit_code} actual={proc.returncode}\n"
                    f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
                )
                result = ValidationResult(
                    check_type=check.type.value,
                    success=success,
                    details=details,
                    exit_code=proc.returncode,
                    check_index=index,
                    failure_provenance=provenance,
                )
            except FileNotFoundError as exc:
                result = ValidationResult(
                    check_type=check.type.value,
                    success=False,
                    details=f"command not found: {exc}",
                    check_index=index,
                    failure_provenance="environment_failure",
                )
            except subprocess.TimeoutExpired as exc:
                result = ValidationResult(
                    check_type=check.type.value,
                    success=False,
                    details=f"validation timeout: {exc}",
                    check_index=index,
                    failure_provenance="timeout",
                )
            except Exception as exc:
                result = ValidationResult(
                    check_type=check.type.value,
                    success=False,
                    details=f"validation execution exception: {exc}",
                    check_index=index,
                    failure_provenance="harness_failure",
                )
            results.append(result)
            if on_check_end:
                on_check_end(index, check, result)
            continue

        file_path = repo_root / (check.path or "")
        if not file_path.exists():
            result = ValidationResult(
                check_type=check.type.value,
                success=False,
                details=f"File not found: {file_path}",
                check_index=index,
                failure_provenance="validation_failure",
            )
            results.append(result)
            if on_check_end:
                on_check_end(index, check, result)
            continue

        content = file_path.read_text(encoding="utf-8")
        substring = check.substring or ""
        if check.type == ValidationCheckType.FILE_CONTAINS:
            success = substring in content
            details = f"substring present={success}"
        else:
            success = substring not in content
            details = f"substring absent={success}"
        result = ValidationResult(
            check_type=check.type.value,
            success=success,
            details=details,
            check_index=index,
            failure_provenance=None if success else "validation_failure",
        )
        results.append(result)
        if on_check_end:
            on_check_end(index, check, result)
    return results


def compute_unnecessary_files_touched(changed_files: list[str], expected_touched_paths: list[str]) -> list[str]:
    if not expected_touched_paths:
        return []
    expected = set(expected_touched_paths)
    return sorted(path for path in changed_files if path not in expected)


def compute_forbidden_files_touched(changed_files: list[str], forbidden_touched_paths: list[str]) -> list[str]:
    if not forbidden_touched_paths:
        return []
    forbidden = set(forbidden_touched_paths)
    return sorted(path for path in changed_files if path in forbidden)


def compute_composite_score(
    task_success: bool,
    forbidden_files_touched_count: int,
    unnecessary_files_touched_count: int,
    catastrophic_failure: bool,
    elapsed_seconds: float,
) -> float:
    score = 0.0
    if task_success:
        score += 100
    score -= 30 * forbidden_files_touched_count
    score -= 10 * unnecessary_files_touched_count
    if catastrophic_failure:
        score -= 20
    score -= min(elapsed_seconds / 10, 20)
    return round(score, 3)
