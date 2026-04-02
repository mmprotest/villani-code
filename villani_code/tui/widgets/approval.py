from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from rich.text import Text
from textual.widgets import Label, ListItem, ListView, Static


class ApprovalBar(Vertical):
    can_focus = True
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
        self._resolved = False

    def compose(self) -> ComposeResult:
        yield Static("", id="approval-prompt")
        yield ListView(id="approval-options")

    def on_mount(self) -> None:
        self.display = False

    def show_request(self, prompt: str, request_id: str, choices: list[str]) -> None:
        self.request_id = request_id
        self._choices = choices
        self._resolved = False
        self.display = True
        self.query_one("#approval-prompt", Static).update(Text(prompt))
        options = self._options()
        options.clear()
        for choice in choices:
            options.append(ListItem(Label(choice.capitalize())))
        options.index = 0
        self._sync_selected_style()
        options.focus()

    def hide_request(self) -> None:
        self.request_id = None
        self.display = False

    def _options(self) -> ListView:
        options = self.query_one("#approval-options", ListView)
        options.can_focus = True
        return options

    def _sync_selected_style(self) -> None:
        options = self._options()
        for idx, child in enumerate(options.children):
            if isinstance(child, ListItem):
                child.set_class(idx == options.index, "selected")

    def action_cursor_up(self) -> None:
        if self.request_id is not None:
            self._options().action_cursor_up()
            self._sync_selected_style()

    def action_cursor_down(self) -> None:
        if self.request_id is not None:
            self._options().action_cursor_down()
            self._sync_selected_style()

    def action_confirm(self) -> None:
        if self.request_id is None or self._resolved:
            return
        index = self._options().index
        if index is None or index < 0 or index >= len(self._choices):
            return
        self._resolved = True
        self.post_message(self.ApprovalSelected(self._choices[index]))

    def action_deny(self) -> None:
        if self.request_id is None or self._resolved:
            return
        self._resolved = True
        self.post_message(self.ApprovalSelected("no"))

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        if self.request_id is not None:
            self._sync_selected_style()

    class ApprovalSelected(Message):
        def __init__(self, choice: str) -> None:
            self.choice = choice
            super().__init__()
