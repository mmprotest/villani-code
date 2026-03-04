from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActiveModal(str, Enum):
    NONE = "none"
    PALETTE = "palette"
    HELP = "help"
    SETTINGS = "settings"
    OUTPUT = "output"


@dataclass(slots=True)
class UIState:
    show_tasks: bool = False
    show_diff: bool = False
    focus_mode: bool = False
    verbose_tool_output: bool = False
    active_modal: ActiveModal = ActiveModal.NONE
    panel_section: str = "tasks"
    selected_task_index: int = 0
    selected_diff_file: int = 0
    selected_diff_hunk: int = 0
    last_error: str = ""
    connected: bool = False
    active_tools: int = 0
    tokens_last_minute: int = 0
    total_tokens: int = 0
    last_tool_name: str = "-"
    output_view_id: str | None = None
