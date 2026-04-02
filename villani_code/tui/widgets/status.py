from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from villani_code.tui.widgets.spinner import SpinnerWidget


class StatusBarWidget(Horizontal):
    def compose(self) -> ComposeResult:
        yield SpinnerWidget()
        yield Static("Idle", id="status-text")
        yield Static("PLAN:OFF", id="plan-mode")
        yield Static("FOLLOW", id="follow-mode")

    def set_status(self, text: str) -> None:
        self.query_one("#status-text", Static).update(Text(text))

    def set_spinner(self, active: bool, label: str | None = None) -> None:
        self.query_one(SpinnerWidget).set_state(active, label)

    def set_follow_mode(self, follow_tail: bool) -> None:
        self.query_one("#follow-mode", Static).update("FOLLOW" if follow_tail else "PAUSED")

    def set_plan_mode(self, enabled: bool) -> None:
        self.query_one("#plan-mode", Static).update("PLAN:ON" if enabled else "PLAN:OFF")
