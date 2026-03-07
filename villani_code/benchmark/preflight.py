from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from villani_code.benchmark.adapters import AVAILABLE_ADAPTERS
from villani_code.benchmark.command_resolution import resolve_command
from villani_code.benchmark.models import BenchmarkTask
from villani_code.benchmark.task_loader import load_benchmark_tasks, load_task_pack_metadata, resolve_tasks_dir


@dataclass(slots=True)
class PreflightResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def run_benchmark_preflight(tasks_dir: Path, repo_path: Path, agents: list[str]) -> PreflightResult:
    result = PreflightResult(ok=True)

    if not repo_path.exists():
        result.ok = False
        result.errors.append(f"Repository path does not exist: {repo_path}")

    try:
        resolved_tasks_dir = resolve_tasks_dir(tasks_dir)
        load_task_pack_metadata(resolved_tasks_dir)
        tasks = load_benchmark_tasks(resolved_tasks_dir)
    except Exception as exc:
        result.ok = False
        result.errors.append(f"Task pack validation failed: {exc}")
        return result

    unknown = [name for name in agents if name not in AVAILABLE_ADAPTERS]
    if unknown:
        result.ok = False
        result.errors.append(f"Unknown agent names: {', '.join(sorted(unknown))}")

    for task in tasks:
        _validate_task_commands(task, result)

    if result.warnings:
        result.notes.append("Warnings indicate likely environment issues; benchmark will proceed but may produce skips or environment failures.")
    return result


def _validate_task_commands(task: BenchmarkTask, result: PreflightResult) -> None:
    for index, check in enumerate(task.validation_checks):
        if check.type.value != "command":
            continue
        if not check.command or not check.command.strip():
            result.ok = False
            result.errors.append(f"Task '{task.id}' has empty validation command at check index {index}.")
            continue
        resolution = resolve_command(check.command)
        if not resolution.resolved.argv:
            result.ok = False
            result.errors.append(f"Task '{task.id}' has unparsable validation command at check index {index}.")
        elif not resolution.executable_found:
            result.warnings.append(
                f"Task '{task.id}' check {index} references executable '{resolution.resolved.argv[0]}' that is not currently resolvable."
            )
