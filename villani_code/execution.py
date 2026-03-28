from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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
    changed_files: list[str]
    intentional_changes: list[str]
    incidental_changes: list[str]
    all_changes: list[str]
    intended_targets: list[str]
    before_contents: dict[str, str]
    validation_artifacts: list[str]
    command_results: list[dict[str, Any]]
    inspection_summary: str
    tool_failures: list[str]
    terminated_reason: str
    patch_detected: bool
    meaningful_patch: bool
    model_activity: dict[str, int]
    prose_only: bool
    acted: bool
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
