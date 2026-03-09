from __future__ import annotations

from dataclasses import dataclass

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig


@dataclass(slots=True)
class BenchmarkExecutionStrategy:
    task_class: str
    max_reads: int
    max_searches: int
    max_bash: int
    max_verification_bash: int
    max_patch_attempts: int
    max_blocked_mutations: int
    allow_repo_wide_search_initially: bool
    prioritize_expected_files: bool
    direct_fix_first: bool
    defer_full_verification_until_patch: bool


def benchmark_strategy_from_config(config: BenchmarkRuntimeConfig) -> BenchmarkExecutionStrategy:
    task_type = (config.task_type or "").strip().lower()
    if task_type == "single_file_bugfix":
        return BenchmarkExecutionStrategy(
            task_class="single_file_bugfix",
            max_reads=6,
            max_searches=4,
            max_bash=4,
            max_verification_bash=2,
            max_patch_attempts=3,
            max_blocked_mutations=2,
            allow_repo_wide_search_initially=False,
            prioritize_expected_files=True,
            direct_fix_first=True,
            defer_full_verification_until_patch=True,
        )
    if task_type.startswith("terminal"):
        return BenchmarkExecutionStrategy(
            task_class="terminal_task",
            max_reads=8,
            max_searches=6,
            max_bash=10,
            max_verification_bash=5,
            max_patch_attempts=3,
            max_blocked_mutations=2,
            allow_repo_wide_search_initially=True,
            prioritize_expected_files=bool(config.expected_files),
            direct_fix_first=False,
            defer_full_verification_until_patch=False,
        )
    if config.requires_repo_navigation:
        return BenchmarkExecutionStrategy(
            task_class="repo_navigation_bugfix",
            max_reads=14,
            max_searches=10,
            max_bash=8,
            max_verification_bash=4,
            max_patch_attempts=4,
            max_blocked_mutations=3,
            allow_repo_wide_search_initially=True,
            prioritize_expected_files=bool(config.expected_files),
            direct_fix_first=False,
            defer_full_verification_until_patch=True,
        )
    return BenchmarkExecutionStrategy(
        task_class="bounded_general",
        max_reads=10,
        max_searches=8,
        max_bash=6,
        max_verification_bash=3,
        max_patch_attempts=3,
        max_blocked_mutations=2,
        allow_repo_wide_search_initially=True,
        prioritize_expected_files=bool(config.expected_files),
        direct_fix_first=True,
        defer_full_verification_until_patch=True,
    )

