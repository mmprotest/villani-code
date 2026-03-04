from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Static


class ApprovalBar(Horizontal):
    def __init__(self) -> None:
        super().__init__(id="approval-bar")
        self.display = False
        self.request_id: str | None = None
        self._choices = ["yes", "always", "no"]
        self._index = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="approval-prompt")
        yield Button("Approve", id="approve-yes")
        yield Button("Approve always", id="approve-always")
        yield Button("Deny", id="approve-no")

    def show_request(self, prompt: str, request_id: str) -> None:
        self.request_id = request_id
        self.display = True
        self._index = 0
        self.query_one("#approval-prompt", Static).update(prompt)
        self._sync_highlight()

    def hide_request(self) -> None:
        self.request_id = None
        self.display = False

    def selected_choice(self) -> str:
        return self._choices[self._index]

    def move(self, delta: int) -> None:
        self._index = (self._index + delta) % len(self._choices)
        self._sync_highlight()

    def _sync_highlight(self) -> None:
        mapping = {"yes": "#approve-yes", "always": "#approve-always", "no": "#approve-no"}
        for choice, selector in mapping.items():
            button = self.query_one(selector, Button)
            button.variant = "primary" if choice == self.selected_choice() else "default"

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = "no"
        if event.button.id == "approve-yes":
            choice = "yes"
        elif event.button.id == "approve-always":
            choice = "always"
        self.post_message(self.ApprovalSelected(choice))

    class ApprovalSelected(Message):
        def __init__(self, choice: str) -> None:
            self.choice = choice
            super().__init__()
