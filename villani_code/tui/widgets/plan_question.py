from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from rich.text import Text
from textual.widgets import Input, Label, ListItem, ListView, Static

from villani_code.plan_session import PlanAnswer, PlanQuestion


class PlanQuestionWidget(Vertical):
    BINDINGS = [
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("enter", "confirm", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="plan-question")
        self._question: PlanQuestion | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="plan-question-text")
        yield Static("", id="plan-question-rationale")
        yield ListView(id="plan-question-options")
        yield Input(placeholder="Provide custom answer", id="plan-other-input")
        yield Static("Press Enter to submit", id="plan-question-submit")

    def on_mount(self) -> None:
        self.display = False
        self.query_one("#plan-other-input", Input).display = False

    def show_question(self, question: PlanQuestion) -> None:
        self._question = question
        self.display = True
        self.query_one("#plan-question-text", Static).update(Text(question.question))
        self.query_one("#plan-question-rationale", Static).update(Text(f"Why this matters: {question.rationale}"))
        options = self.query_one("#plan-question-options", ListView)
        options.can_focus = True
        options.clear()
        for option in question.options:
            options.append(ListItem(Label(f"{option.label} — {option.description}")))
        options.index = 0
        self._sync_selected_style()
        self._sync_other_visibility()
        options.focus()

    def hide_question(self) -> None:
        self.display = False
        self._question = None

    def _selected_index(self) -> int:
        options = self.query_one("#plan-question-options", ListView)
        return 0 if options.index is None else options.index

    def _sync_selected_style(self) -> None:
        options = self.query_one("#plan-question-options", ListView)
        for idx, child in enumerate(options.children):
            if isinstance(child, ListItem):
                child.set_class(idx == options.index, "selected")

    def _selected_option_is_other(self) -> bool:
        if self._question is None:
            return False
        idx = self._selected_index()
        if idx < 0 or idx >= len(self._question.options):
            return False
        return self._question.options[idx].is_other

    def _sync_other_visibility(self) -> None:
        other_input = self.query_one("#plan-other-input", Input)
        if self._selected_option_is_other():
            other_input.display = True
            other_input.focus()
        else:
            other_input.display = False
            other_input.value = ""
            self.query_one("#plan-question-options", ListView).focus()

    def action_cursor_up(self) -> None:
        if not self.display:
            return
        options = self.query_one("#plan-question-options", ListView)
        options.action_cursor_up()
        self._sync_selected_style()
        self._sync_other_visibility()

    def action_cursor_down(self) -> None:
        if not self.display:
            return
        options = self.query_one("#plan-question-options", ListView)
        options.action_cursor_down()
        self._sync_selected_style()
        self._sync_other_visibility()

    def _build_answer(self) -> tuple[PlanAnswer | None, str | None]:
        if self._question is None:
            return None, "No active question."
        idx = self._selected_index()
        if idx < 0 or idx >= len(self._question.options):
            return None, "Select an option first."
        selected = self._question.options[idx]
        other_text = ""
        if selected.is_other:
            other_text = self.query_one("#plan-other-input", Input).value.strip()
            if not other_text:
                return None, "Other requires non-empty text."
        return PlanAnswer(question_id=self._question.id, selected_option_id=selected.id, other_text=other_text), None

    def action_confirm(self) -> None:
        if not self.display:
            return
        answer, err = self._build_answer()
        if err:
            self.post_message(self.InvalidAnswer(err))
            return
        assert answer is not None
        self.post_message(self.AnswerSubmitted(answer))

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        if self.display:
            self._sync_selected_style()
            self._sync_other_visibility()


    def option_labels(self) -> list[str]:
        if self._question is None:
            return []
        return [option.label for option in self._question.options]

    class AnswerSubmitted(Message):
        def __init__(self, answer: PlanAnswer) -> None:
            self.answer = answer
            super().__init__()

    class InvalidAnswer(Message):
        def __init__(self, reason: str) -> None:
            self.reason = reason
            super().__init__()
