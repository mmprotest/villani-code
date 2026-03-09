from __future__ import annotations

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.execution import execution_budget_from_benchmark_config


def test_single_file_bugfix_gets_tight_budget() -> None:
    budget = execution_budget_from_benchmark_config(
        BenchmarkRuntimeConfig(enabled=True, task_type="single_file_bugfix")
    )
    assert budget.max_seconds <= 180
    assert budget.max_turns <= 10


def test_repo_navigation_gets_broader_budget() -> None:
    budget = execution_budget_from_benchmark_config(
        BenchmarkRuntimeConfig(enabled=True, task_type="bugfix", requires_repo_navigation=True)
    )
    assert budget.max_seconds >= 400
    assert budget.max_tool_calls >= 40


def test_terminal_gets_more_bash_budget_policy() -> None:
    budget = execution_budget_from_benchmark_config(
        BenchmarkRuntimeConfig(enabled=True, task_type="terminal_workflow")
    )
    assert budget.max_tool_calls >= 30
