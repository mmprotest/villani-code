"""Compatibility wrappers for legacy top-level UI imports.

Canonical implementations live in ``villani_code.tui.components``.
"""

from villani_code.tui.components import (
    CommandAction,
    CommandPalette,
    SettingsManager,
    StatusBar,
    StatusSnapshot,
    TaskEvent,
    TaskManager,
    TaskStatus,
    UserSettings,
)

__all__ = [
    "CommandAction",
    "CommandPalette",
    "SettingsManager",
    "StatusBar",
    "StatusSnapshot",
    "TaskEvent",
    "TaskManager",
    "TaskStatus",
    "UserSettings",
]
