from __future__ import annotations

import subprocess
from pathlib import Path

from villani_code.orchestrator_git import changed_files, diff_line_count, has_diff
from villani_code.orchestrator_models import VerificationResult, WorkerTask


MAX_CHANGED_FILES = 5
MAX_CHANGED_LINES = 250


def _run_commands(repo: Path, commands: list[str]) -> tuple[bool, list[str]]:
    executed: list[str] = []
    for command in commands:
        cmd = command.strip()
        if not cmd:
            continue
        proc = subprocess.run(cmd, cwd=repo, shell=True, check=False, capture_output=True, text=True)
        executed.append(cmd)
        if proc.returncode != 0:
            return False, executed
    return True, executed


def verify_worker_result(worktree: Path, task: WorkerTask, recommended_verification: list[str] | None) -> VerificationResult:
    if not has_diff(worktree):
        return VerificationResult(status="hard_failure", summary="Worker produced no changes", commands_run=[], files_touched=[])

    files = changed_files(worktree)
    if task.target_files:
        unrelated = [f for f in files if f not in set(task.target_files)]
        if unrelated:
            return VerificationResult(
                status="hard_failure",
                summary=f"Edited files outside scope: {', '.join(unrelated[:5])}",
                commands_run=[],
                files_touched=files,
            )

    if len(files) > MAX_CHANGED_FILES:
        return VerificationResult(
            status="retryable_failure",
            summary=f"Too many files changed ({len(files)} > {MAX_CHANGED_FILES})",
            commands_run=[],
            files_touched=files,
        )

    lines = diff_line_count(worktree)
    if lines > MAX_CHANGED_LINES:
        return VerificationResult(
            status="retryable_failure",
            summary=f"Diff too large ({lines} > {MAX_CHANGED_LINES} changed lines)",
            commands_run=[],
            files_touched=files,
        )

    commands: list[str] = []
    commands.extend(task.success_criteria)
    if recommended_verification:
        commands.extend(recommended_verification)
    if not commands and (worktree / "pyproject.toml").exists():
        commands.append("python -m pytest -q -k not slow")

    ok, executed = _run_commands(worktree, commands)
    if not ok:
        return VerificationResult(
            status="retryable_failure",
            summary="Deterministic verification command failed",
            commands_run=executed,
            files_touched=files,
        )

    return VerificationResult(status="accepted", summary="Worker changes accepted", commands_run=executed, files_touched=files)


def run_final_verification(repo: Path) -> VerificationResult:
    commands: list[str] = []
    if (repo / "pyproject.toml").exists():
        commands.append("python -m pytest -q -k not slow")
    elif (repo / "Makefile").exists():
        commands.append("make -n test")
    ok, executed = _run_commands(repo, commands)
    if not ok:
        return VerificationResult(status="hard_failure", summary="Final verification failed", commands_run=executed, files_touched=[])
    return VerificationResult(status="accepted", summary="Final verification passed", commands_run=executed, files_touched=[])
