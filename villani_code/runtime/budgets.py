from __future__ import annotations

from villani_code.benchmark.models import TaskFamily
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.schemas import RuntimeBudgets


def select_runtime_budgets(config: BenchmarkRuntimeConfig, task_family: str | None = None, max_files_touched: int = 1) -> RuntimeBudgets:
    budgets = RuntimeBudgets()
    is_multi_file = max_files_touched > 1
    if task_family == TaskFamily.LOCALIZE_PATCH.value and max_files_touched > 1:
        is_multi_file = True
    if is_multi_file:
        budgets.max_patch_lines = 40
        budgets.max_files_per_patch = 2
    budgets.max_files_per_patch = min(budgets.max_files_per_patch, max(1, config.max_files_touched))
    return budgets


def timeout_imminent(started: float, now: float, timeout_seconds: float, avg_cycle_seconds: float, safety_factor: float = 1.25) -> bool:
    elapsed = max(0.0, now - started)
    remaining = max(0.0, timeout_seconds - elapsed)
    needed = max(1.0, avg_cycle_seconds * safety_factor)
    return remaining < needed
