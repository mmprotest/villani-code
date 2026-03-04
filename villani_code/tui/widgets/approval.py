from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, Static


class ApprovalBar(Vertical):
    BINDINGS = [
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("enter", "confirm", show=False),
        Binding("escape", "deny", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="approval-bar")
        self.request_id: str | None = None
        self._choices: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="approval-prompt")
        yield OptionList(id="approval-options")

    def on_mount(self) -> None:
        self.display = False

    def show_request(self, prompt: str, request_id: str, choices: list[str]) -> None:
        self.request_id = request_id
        self._choices = choices
        self.display = True
        self.query_one("#approval-prompt", Static).update(prompt)
        options = self.query_one("#approval-options", OptionList)
        options.clear_options()
        options.add_options([choice.capitalize() for choice in choices])
        options.styles.height = len(choices)
        options.styles.min_height = len(choices)
        self.styles.height = 1 + len(choices)
        self.styles.min_height = 1 + len(choices)
        options.highlighted = 0
        options.focus()

    def hide_request(self) -> None:
        self.request_id = None
        self.display = False

    def _options(self) -> OptionList:
        return self.query_one("#approval-options", OptionList)

    def action_cursor_up(self) -> None:
        if self.request_id is not None:
            self._options().action_cursor_up()

    def action_cursor_down(self) -> None:
        if self.request_id is not None:
            self._options().action_cursor_down()

    def action_confirm(self) -> None:
        if self.request_id is None:
            return
        index = self._options().highlighted
        if index is None or index < 0 or index >= len(self._choices):
            return
        self.post_message(self.ApprovalSelected(self._choices[index]))

    def action_deny(self) -> None:
        if self.request_id is not None:
            self.post_message(self.ApprovalSelected("no"))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.request_id is None:
            return
        if 0 <= event.option_index < len(self._choices):
            self.post_message(self.ApprovalSelected(self._choices[event.option_index]))

    class ApprovalSelected(Message):
        def __init__(self, choice: str) -> None:
            self.choice = choice
            super().__init__()
