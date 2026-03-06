"""Canonical lightweight UI components used by the TUI and tests."""

from villani_code.tui.components.command_palette import CommandAction, CommandPalette
from villani_code.tui.components.diff_viewer import DiffViewer
from villani_code.tui.components.settings import SettingsManager, UserSettings
from villani_code.tui.components.status_bar import StatusBar, StatusSnapshot
from villani_code.tui.components.task_board import TaskEvent, TaskManager, TaskStatus

__all__ = [
    "CommandAction",
    "CommandPalette",
    "DiffViewer",
    "SettingsManager",
    "StatusBar",
    "StatusSnapshot",
    "TaskEvent",
    "TaskManager",
    "TaskStatus",
    "UserSettings",
]
