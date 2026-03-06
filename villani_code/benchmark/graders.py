from __future__ import annotations

import subprocess
from pathlib import Path

from villani_code.benchmark.adapters.base import ValidationResult
from villani_code.benchmark.models import BenchmarkTask, ValidationCheckType


def execute_validation_checks(task: BenchmarkTask, repo_root: Path) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    for index, check in enumerate(task.validation_checks):
        if check.type == ValidationCheckType.COMMAND:
            cwd = repo_root if check.cwd is None else (repo_root / check.cwd)
            proc = subprocess.run(
                ["bash", "-lc", check.command or ""],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            success = proc.returncode == check.expect_exit_code
            details = f"expected={check.expect_exit_code} actual={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
            results.append(
                ValidationResult(
                    check_type=check.type.value,
                    success=success,
                    details=details,
                    exit_code=proc.returncode,
                    check_index=index,
                )
            )
            continue

        file_path = repo_root / (check.path or "")
        if not file_path.exists():
            results.append(
                ValidationResult(
                    check_type=check.type.value,
                    success=False,
                    details=f"File not found: {file_path}",
                    check_index=index,
                )
            )
            continue

        content = file_path.read_text(encoding="utf-8")
        substring = check.substring or ""
        if check.type == ValidationCheckType.FILE_CONTAINS:
            success = substring in content
            details = f"substring present={success}"
        else:
            success = substring not in content
            details = f"substring absent={success}"
        results.append(
            ValidationResult(
                check_type=check.type.value,
                success=success,
                details=details,
                check_index=index,
            )
        )
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
