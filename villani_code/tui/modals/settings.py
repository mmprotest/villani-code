from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.widgets import Checkbox, Dialog, Label

from ui.settings import UserSettings


class SettingsModal:
    def __init__(self, settings: UserSettings, on_apply: Callable[[bool, bool], None]) -> None:
        self.verbose = Checkbox(text="Verbose tool output", checked=settings.verbose)
        self.auto_accept = Checkbox(text="Auto accept edits", checked=settings.auto_accept_edits)
        self.container = Dialog(title="Settings", body=Label(text="Toggle settings with space and close with Esc"))
        self.on_apply = on_apply

    def apply(self) -> None:
        self.on_apply(self.verbose.checked, self.auto_accept.checked)
