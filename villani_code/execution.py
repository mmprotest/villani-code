from __future__ import annotations

from dataclasses import asdict, dataclass

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig


@dataclass(slots=True)
class ExecutionBudget:
    max_turns: int
    max_tool_calls: int
    max_seconds: float
    max_no_edit_turns: int
    max_reconsecutive_recon_turns: int


@dataclass(slots=True)
class ExecutionResult:
    final_text: str
    turns_used: int
    tool_calls_used: int
    elapsed_seconds: float
    files_changed: list[str]
    intentional_changes: list[str]
    incidental_changes: list[str]
    all_changes: list[str]
    intended_targets: list[str]
    before_contents: dict[str, str]
    validation_artifacts: list[str]
    inspection_summary: str
    runner_failures: list[str]
    terminated_reason: str
    completed: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


VILLANI_TASK_BUDGET = ExecutionBudget(
    max_turns=20,
    max_tool_calls=40,
    max_seconds=180.0,
    max_no_edit_turns=8,
    max_reconsecutive_recon_turns=6,
)


def execution_budget_from_benchmark_config(config: BenchmarkRuntimeConfig) -> ExecutionBudget:
    task_type = (config.task_type or "").strip().lower()
    low_scope = task_type in {"single_file_bugfix", "single_file", "small_bugfix"}
    repo_nav = bool(config.requires_repo_navigation)
    terminal = task_type.startswith("terminal")

    if low_scope and not repo_nav:
        return ExecutionBudget(
            max_turns=10,
            max_tool_calls=22,
            max_seconds=180.0,
            max_no_edit_turns=4,
            max_reconsecutive_recon_turns=3,
        )
    if terminal:
        return ExecutionBudget(
            max_turns=14,
            max_tool_calls=34,
            max_seconds=300.0,
            max_no_edit_turns=6,
            max_reconsecutive_recon_turns=4,
        )
    if repo_nav or config.requires_multi_step_reasoning:
        return ExecutionBudget(
            max_turns=16,
            max_tool_calls=40,
            max_seconds=420.0,
            max_no_edit_turns=7,
            max_reconsecutive_recon_turns=5,
        )
    return ExecutionBudget(
        max_turns=12,
        max_tool_calls=28,
        max_seconds=240.0,
        max_no_edit_turns=5,
        max_reconsecutive_recon_turns=4,
    )
