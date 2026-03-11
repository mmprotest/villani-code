from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class EditBudget:
    max_files: int
    max_lines: int
    max_hunks: int = 4


def within_edit_budget(files_touched: list[str], changed_lines: int, hunks: int, budget: EditBudget) -> bool:
    return len(set(files_touched)) <= budget.max_files and changed_lines <= budget.max_lines and hunks <= budget.max_hunks
