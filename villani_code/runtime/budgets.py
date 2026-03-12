from __future__ import annotations

from villani_code.benchmark.models import TaskFamily
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.policy import WeakSearchPolicyProfile
from villani_code.runtime.schemas import RuntimeBudgets


def select_runtime_budgets(
    config: BenchmarkRuntimeConfig,
    task_family: str | None = None,
    max_files_touched: int = 1,
    policy_profile: str | WeakSearchPolicyProfile = WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH,
) -> RuntimeBudgets:
    budgets = RuntimeBudgets()
    profile = WeakSearchPolicyProfile(str(policy_profile))

    if profile == WeakSearchPolicyProfile.FAST_PATH_SINGLE_FILE:
        budgets.max_cycles = 2
        budgets.max_active_branches = 2
        budgets.max_hypotheses_per_suspect = 2
        budgets.max_candidates_per_hypothesis = 1
        budgets.max_candidate_turns = 3
        budgets.max_candidate_tool_calls = 8
        budgets.max_patch_lines = 16
        budgets.max_files_per_patch = 1
        budgets.max_consecutive_no_improvement_cycles = 1
    elif profile == WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH:
        budgets.max_cycles = 4
        budgets.max_active_branches = 4
        budgets.max_hypotheses_per_suspect = 4
        budgets.max_candidates_per_hypothesis = 2
        budgets.max_candidate_turns = 5
        budgets.max_candidate_tool_calls = 16
        budgets.max_consecutive_no_improvement_cycles = 2
    else:
        budgets.max_cycles = 8
        budgets.max_active_branches = 6
        budgets.max_hypotheses_per_suspect = 5
        budgets.max_candidates_per_hypothesis = 2
        budgets.max_candidate_turns = 8
        budgets.max_candidate_tool_calls = 24
        budgets.max_consecutive_no_improvement_cycles = 2

    is_multi_file = max_files_touched > 1
    if task_family == TaskFamily.LOCALIZE_PATCH.value and max_files_touched > 1:
        is_multi_file = True
    if is_multi_file:
        budgets.max_patch_lines = max(budgets.max_patch_lines, 40)
        budgets.max_files_per_patch = max(budgets.max_files_per_patch, 2)

    budgets.max_files_per_patch = min(budgets.max_files_per_patch, max(1, config.max_files_touched))
    return budgets


def timeout_imminent(started: float, now: float, timeout_seconds: float, avg_cycle_seconds: float, safety_factor: float = 1.25) -> bool:
    elapsed = max(0.0, now - started)
    remaining = max(0.0, timeout_seconds - elapsed)
    needed = max(1.0, avg_cycle_seconds * safety_factor)
    return remaining < needed
