from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from villani_code.tui.widgets.spinner import SpinnerWidget


class StatusBarWidget(Horizontal):
    def compose(self) -> ComposeResult:
        yield SpinnerWidget()
        yield Static("Idle", id="status-text")

    def set_status(self, text: str) -> None:
        self.query_one("#status-text", Static).update(text)

    def set_spinner(self, active: bool, label: str | None = None) -> None:
        self.query_one(SpinnerWidget).set_state(active, label)
