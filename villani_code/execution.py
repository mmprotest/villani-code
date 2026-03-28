from __future__ import annotations

from dataclasses import asdict, dataclass


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
    structured_validation_results: list[dict[str, object]]
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
