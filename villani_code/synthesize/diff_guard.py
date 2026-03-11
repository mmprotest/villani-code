from __future__ import annotations

from dataclasses import dataclass

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.synthesize.edit_budget import EditBudget, within_edit_budget


@dataclass(slots=True)
class DiffGuardDecision:
    allowed: bool
    reason: str


def guard_candidate_diff(
    *,
    files_touched: list[str],
    changed_lines: int,
    hunks: int,
    budget: EditBudget,
    benchmark_config: BenchmarkRuntimeConfig,
    adds_new_file: bool = False,
    formatting_only: bool = False,
) -> DiffGuardDecision:
    norm_files = [f.replace("\\", "/").lstrip("./") for f in files_touched]
    if not within_edit_budget(norm_files, changed_lines, hunks, budget):
        return DiffGuardDecision(False, "edit_budget_exceeded")
    if formatting_only:
        return DiffGuardDecision(False, "formatting_only_churn")
    if adds_new_file and not any(benchmark_config.is_expected_or_support(path) for path in norm_files):
        return DiffGuardDecision(False, "scratch_file_not_allowlisted")
    for path in norm_files:
        if benchmark_config.in_forbidden(path):
            return DiffGuardDecision(False, "forbidden_path")
        if benchmark_config.enabled and not benchmark_config.in_allowlist(path) and not benchmark_config.is_expected_or_support(path):
            return DiffGuardDecision(False, "outside_scope")
    return DiffGuardDecision(True, "ok")
