from __future__ import annotations

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.benchmark.strategy import benchmark_strategy_from_config


def test_single_file_bugfix_strategy_is_tight() -> None:
    strategy = benchmark_strategy_from_config(
        BenchmarkRuntimeConfig(enabled=True, task_type="single_file_bugfix", expected_files=["src/app.py"])
    )
    assert strategy.task_class == "single_file_bugfix"
    assert strategy.max_reads <= 6
    assert strategy.direct_fix_first is True
    assert strategy.prioritize_expected_files is True


def test_repo_navigation_strategy_is_broader() -> None:
    strategy = benchmark_strategy_from_config(
        BenchmarkRuntimeConfig(enabled=True, task_type="bugfix", requires_repo_navigation=True)
    )
    assert strategy.task_class == "repo_navigation_bugfix"
    assert strategy.max_reads >= 10
    assert strategy.allow_repo_wide_search_initially is True


def test_terminal_strategy_has_more_bash() -> None:
    strategy = benchmark_strategy_from_config(
        BenchmarkRuntimeConfig(enabled=True, task_type="terminal_001")
    )
    assert strategy.task_class == "terminal_task"
    assert strategy.max_bash >= 10
