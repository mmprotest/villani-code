from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Key, MouseScrollDown, MouseScrollUp
from textual.timer import Timer
from textual.widgets import Input, Static

from villani_code.interrupts import InterruptController
from villani_code.plan_session import PlanAnswer, PlanQuestion, PlanSessionResult
from villani_code.tui.assets import LAUNCH_BANNER
from villani_code.tui.components.command_palette import CommandPalette, PaletteItem
from villani_code.tui.controller import RunnerController
from villani_code.tui.messages import ApprovalRequest, LogAppend, SpinnerState, StatusUpdate
from villani_code.tui.widgets.approval import ApprovalBar
from villani_code.tui.widgets.plan_question import PlanQuestionWidget
from villani_code.tui.widgets.slash_popup import SlashCommandPopup
from villani_code.tui.widgets.status import StatusBarWidget


def _render_plan_lines(result: PlanSessionResult) -> list[str]:
    lines: list[str] = [
        "Plan:",
        f"- Objective: {result.task_summary}",
        f"- Risk level: {result.risk_level}",
        f"- Confidence: {result.confidence_score:.2f}",
    ]

    if result.candidate_files:
        lines.append("- Files to inspect/update:")
        lines.extend(f"  - {path}" for path in result.candidate_files)

    if result.recommended_steps:
        lines.append("- Implementation steps:")
        lines.extend(f"  - {step}" for step in result.recommended_steps)

    if result.assumptions:
        lines.append("- Assumptions, risks, and validation:")
        lines.extend(f"  - {item}" for item in result.assumptions)

    return lines

class VillaniTranscript(VerticalScroll):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._content_text = ""

    def compose(self) -> ComposeResult:
        yield Static(Text(""), id="log-content", markup=False)

    @property
    def plain_text(self) -> str:
        return self._content_text

    def _scroll_step(self, event: MouseScrollUp | MouseScrollDown) -> int:
        base = 12
        if getattr(event, "shift", False):
            return base * 3
        if getattr(event, "ctrl", False) or getattr(event, "control", False):
            return base * 8
        return base

    def append_text(self, text: str, follow_tail: bool) -> None:
        self._content_text += text
        self.query_one("#log-content", Static).update(Text(self._content_text))
        if follow_tail:
            self.scroll_end(animate=False)

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        app = self.app
        if isinstance(app, VillaniTUI):
            app.set_follow_tail(False)
        self.scroll_relative(y=-self._scroll_step(event))
        event.stop()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self.scroll_relative(y=self._scroll_step(event))
        if self.is_vertical_scroll_end:
            app = self.app
            if isinstance(app, VillaniTUI):
                app.set_follow_tail(True)
        event.stop()


class VillaniTUI(App[None]):
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("ctrl+c", "interrupt_or_quit", show=False, priority=True),
        Binding("ctrl+shift+c", "copy_console", show=False, priority=True),
    ]

    def __init__(self, runner: Any, repo: Path, villani_mode: bool = False, villani_objective: str | None = None) -> None:
        super().__init__()
        self.runner = runner
        self.repo = repo
        self.follow_tail = True
        self.follow_paused = False
        self._ai_streaming = False
        self._ai_started = False
        self._stream_buffer = ""
        self._log_plain_text = ""
        self._stream_flush_timer: Timer | None = None
        self._interrupts = InterruptController()
        self.villani_mode = villani_mode
        self.villani_objective = villani_objective
        self.controller = RunnerController(runner, self)
        self.command_palette = CommandPalette()

        self.plan_mode_enabled = False
        self.plan_session_active = False
        self.awaiting_plan_prompt = False
        self.pending_plan_instruction = ""
        self.current_plan_result: PlanSessionResult | None = None
        self.pending_questions: list[PlanQuestion] = []
        self.question_cursor = 0
        self.captured_answers: list[PlanAnswer] = []
        self.last_ready_plan: PlanSessionResult | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield VillaniTranscript(id="log")
            yield ApprovalBar()
            yield PlanQuestionWidget()
            yield StatusBarWidget(id="status")
            with Vertical(id="input-area"):
                yield SlashCommandPopup()
                with Horizontal(id="input-row"):
                    yield Static("Villani Code >", id="input-prompt")
                    yield Input(id="input")

    def on_mount(self) -> None:
        log = self.query_one(VillaniTranscript)
        for line in LAUNCH_BANNER.splitlines():
            self._append_log_line(log, line)
        self._append_log_line(log, f"Model: {getattr(self.runner, 'model', 'unknown')}")
        if self.villani_mode:
            objective = self.villani_objective or "inspect and improve this repository autonomously"
            self._append_log_line(log, f"Villani mode active: {objective}")
            self.query_one(StatusBarWidget).set_status("scanning repo")
            input_widget = self.query_one(Input)
            input_widget.disabled = True
            self.controller.run_villani_mode()
        else:
            self._append_log_line(log, "Ready. Type /help for commands.")
            self.query_one(Input).focus()
        self.query_one(StatusBarWidget).set_follow_mode(self.follow_tail)
        self.query_one(StatusBarWidget).set_plan_mode(self.plan_mode_enabled)

    def apply_plan_result(self, result: PlanSessionResult, reset_answers: bool) -> None:
        self.current_plan_result = result
        self.plan_session_active = True
        self.awaiting_plan_prompt = False
        self.plan_mode_enabled = bool(result.open_questions)
        self.pending_questions = list(result.open_questions)
        self.question_cursor = len(self.captured_answers)
        if reset_answers:
            self.captured_answers = []
            self.question_cursor = 0
        if result.ready_to_execute:
            self.last_ready_plan = result
        self._log_local_meta("\n".join(_render_plan_lines(result)))
        self._show_current_question_or_finalize()
        self.query_one(StatusBarWidget).set_plan_mode(bool(result.open_questions))

    def _set_question_mode(self, enabled: bool) -> None:
        input_widget = self.query_one(Input)
        input_widget.disabled = enabled
        if not enabled:
            input_widget.focus()

    def _show_current_question_or_finalize(self) -> None:
        widget = self.query_one(PlanQuestionWidget)
        if self.question_cursor < len(self.pending_questions):
            question = self.pending_questions[self.question_cursor]
            self._set_question_mode(True)
            lines = [
                f"Clarification {self.question_cursor + 1}/{len(self.pending_questions)}: {question.question}",
                *[f"[{idx}] {option.label}" for idx, option in enumerate(question.options, start=1)],
            ]
            self._log_local_meta("\n".join(lines))
            self.query_one(StatusBarWidget).set_status("Plan awaiting clarification")
            widget.show_question(question)
            self.call_after_refresh(lambda: widget.scroll_visible(animate=False))
            return
        widget.hide_question()
        self._set_question_mode(False)
        if self.current_plan_result and self.current_plan_result.ready_to_execute:
            self.plan_mode_enabled = False
            self.plan_session_active = False
            self.query_one(StatusBarWidget).set_status("Plan ready")
            self.query_one(StatusBarWidget).set_plan_mode(False)
            self._log_local_meta("Plan ready. Run /execute to implement.")

    def record_plan_answer(self, answer: PlanAnswer) -> None:
        self.captured_answers.append(answer)
        self.question_cursor += 1
        self._log_local_meta(f"Answer recorded for {answer.question_id}: {answer.selected_option_id}")

    def get_plan_instruction(self) -> str:
        return self.pending_plan_instruction

    def get_plan_answers(self) -> list[PlanAnswer]:
        return list(self.captured_answers)

    def get_last_ready_plan(self) -> PlanSessionResult | None:
        if self.current_plan_result and self.current_plan_result.ready_to_execute:
            return self.current_plan_result
        return self.last_ready_plan

    def _append_log(self, log: VillaniTranscript, text: str) -> None:
        log.append_text(text, follow_tail=self.follow_tail)
        self._log_plain_text += text

    def _append_log_line(self, log: VillaniTranscript, text: str) -> None:
        log.append_text(f"{text}\n", follow_tail=self.follow_tail)
        self._log_plain_text += f"{text}\n"

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            self.copy_to_clipboard(text)
            return
        except Exception:
            pass
        try:
            import pyperclip

            pyperclip.copy(text)
            return
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Clipboard copy failed") from exc

    def action_copy_console(self) -> None:
        try:
            self._copy_to_clipboard(self._log_plain_text.rstrip("\n"))
            self.post_message(StatusUpdate("Copied console text to clipboard."))
        except Exception:
            self.post_message(StatusUpdate("Failed to copy console text."))

    def _log_local_meta(self, text: str) -> None:
        if self.is_running:
            self.post_message(LogAppend(text, kind="meta"))
            return
        self._log_plain_text += f"{text}\n"

    def _slash_popup(self) -> SlashCommandPopup | None:
        try:
            return self.query_one(SlashCommandPopup)
        except NoMatches:
            return None

    def _refresh_slash_popup(self, value: str) -> None:
        popup = self._slash_popup()
        if popup is None:
            return
        query = value.strip()
        if not query.startswith("/"):
            popup.hide_popup()
            return
        matches = [item for _, item in self.command_palette.search_commands(query)]
        popup.set_suggestions(matches)

    def _close_slash_popup(self) -> None:
        popup = self._slash_popup()
        if popup is not None:
            popup.hide_popup()

    def _execute_command_item(self, item: PaletteItem) -> None:
        trigger = item.trigger
        if trigger == "/help":
            self._show_help()
            return
        if trigger == "/plan":
            self.awaiting_plan_prompt = True
            self.plan_mode_enabled = True
            self.plan_session_active = False
            self._log_local_meta("Enter a planning prompt for read-only plan generation.")
            status = self.query_one(StatusBarWidget)
            status.set_status("Awaiting plan prompt")
            status.set_plan_mode(True)
            return
        if trigger == "/cancelplan":
            self.plan_session_active = False
            self.awaiting_plan_prompt = False
            self.pending_plan_instruction = ""
            self.current_plan_result = None
            self.pending_questions = []
            self.question_cursor = 0
            self.captured_answers = []
            self.last_ready_plan = None
            self.query_one(PlanQuestionWidget).hide_question()
            self._set_question_mode(False)
            status = self.query_one(StatusBarWidget)
            status.set_status("Plan canceled")
            status.set_plan_mode(False)
            self._log_local_meta("Planning session canceled.")
            return
        if trigger == "/replan":
            self.controller.replan()
            return
        if trigger == "/execute":
            if self.current_plan_result and not self.current_plan_result.ready_to_execute:
                self._log_local_meta("Cannot execute: unresolved clarifications. Finish answers or use /replan.")
                return
            self.awaiting_plan_prompt = False
            self.plan_mode_enabled = False
            self.plan_session_active = False
            self.query_one(StatusBarWidget).set_plan_mode(False)
            self.query_one(PlanQuestionWidget).hide_question()
            self._set_question_mode(False)
            self.controller.run_execute_plan()
            return
        if trigger == "/fork":
            _ = self.controller.fork_session_context()
            self.controller.reset_session_context()
            self._log_local_meta("Started a fresh session context. (/fork groundwork: old context is cloneable for future forked panes.)")
            return
        self._log_local_meta(f"{trigger} is not implemented yet in this build.")

    def _start_plan(self, prompt: str) -> None:
        self.awaiting_plan_prompt = False
        self.plan_mode_enabled = True
        self.plan_session_active = True
        self.pending_plan_instruction = prompt
        self._log_local_meta("Creating read-only implementation plan...")
        status = self.query_one(StatusBarWidget)
        status.set_status("Planning")
        status.set_plan_mode(True)
        self.controller.run_plan_prompt(prompt)

    def _show_help(self) -> None:
        lines = ["Available slash commands:"]
        for item in self.command_palette.slash_items():
            lines.append(f"  {item.trigger:<12} {item.description}")
        self._log_local_meta("\n".join(lines))

    def _handle_slash_command(self, text: str) -> bool:
        if not text.startswith("/"):
            return False
        command, _, remainder = text.partition(" ")
        normalized = command.lower()
        if normalized == "/plan" and remainder.strip():
            self._start_plan(remainder.strip())
            return True
        item = self.command_palette.command_by_trigger(normalized)
        if item is None:
            self._log_local_meta(f"Unknown command: {normalized}. Type /help for commands.")
            return True
        self._execute_command_item(item)
        return True

    @on(Input.Changed)
    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_slash_popup(event.value)

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self._close_slash_popup()
        if not text:
            return
        if text == "/exit":
            self.exit()
            return
        if self._handle_slash_command(text):
            if not self.query_one(Input).disabled:
                self.query_one(Input).focus()
            return
        self._interrupts.reset_interrupt_state()
        if self.awaiting_plan_prompt:
            self._start_plan(text)
            return
        self.controller.run_prompt(text)

    @on(PlanQuestionWidget.AnswerSubmitted)
    def on_plan_answer_submitted(self, event: PlanQuestionWidget.AnswerSubmitted) -> None:
        self.controller.submit_plan_answer(event.answer)

    @on(PlanQuestionWidget.InvalidAnswer)
    def on_plan_answer_invalid(self, event: PlanQuestionWidget.InvalidAnswer) -> None:
        self._log_local_meta(event.reason)

    def action_interrupt_or_quit(self) -> None:
        action = self._interrupts.register_interrupt()
        if action == "exit":
            self.exit()
            return
        self.post_message(StatusUpdate("Interrupted current session. Press Ctrl+C again to exit Villani Code."))

    def _end_ai_stream_if_open(self, log: VillaniTranscript) -> None:
        self._flush_stream_buffer(log)
        if self._ai_streaming:
            self._append_log(log, "\n")
            self._ai_streaming = False

    def set_follow_tail(self, enabled: bool) -> None:
        self.follow_tail = enabled
        self.follow_paused = not enabled
        self.query_one(StatusBarWidget).set_follow_mode(enabled)

    def _schedule_stream_flush(self) -> None:
        if self._stream_flush_timer is None:
            self._stream_flush_timer = self.set_timer(0.04, self._flush_stream_timer)

    def _flush_stream_timer(self) -> None:
        self._stream_flush_timer = None
        self._flush_stream_buffer(self.query_one(VillaniTranscript))

    def _flush_stream_buffer(self, log: VillaniTranscript) -> None:
        if not self._stream_buffer:
            return
        parts = self._stream_buffer.split("\n")
        self._append_log(log, parts[0])
        for line in parts[1:]:
            self._append_log_line(log, line)
        self._stream_buffer = ""

    def _start_ai_boundary(self, log: VillaniTranscript) -> None:
        if self._ai_started:
            return
        self._ai_started = True

    def on_log_append(self, message: LogAppend) -> None:
        log = self.query_one(VillaniTranscript)
        text = message.text
        kind = message.kind

        if kind in {"user", "meta"}:
            self._end_ai_stream_if_open(log)
            self._ai_started = False
            self._append_log(log, f"{text}\n")
            return

        if kind == "ai":
            self._end_ai_stream_if_open(log)
            self._start_ai_boundary(log)
            for line in text.rstrip("\n").split("\n"):
                self._append_log_line(log, line)
            self._ai_streaming = False
            return

        if kind == "stream":
            if not self._ai_streaming:
                self._start_ai_boundary(log)
                self._ai_streaming = True
                self.set_follow_tail(True)
            self._stream_buffer += text
            self._schedule_stream_flush()
            return

        self._end_ai_stream_if_open(log)
        self._ai_started = False
        self._append_log(log, f"{text}\n")

    def on_status_update(self, message: StatusUpdate) -> None:
        self.query_one(StatusBarWidget).set_status(message.text)

    def on_spinner_state(self, message: SpinnerState) -> None:
        self.query_one(StatusBarWidget).set_spinner(message.active, message.label)

    def on_approval_request(self, message: ApprovalRequest) -> None:
        bar = self.query_one(ApprovalBar)
        self.query_one(Input).disabled = True
        bar.show_request(message.prompt, message.request_id, message.choices)
        self.call_after_refresh(lambda: bar.query_one("#approval-options").focus())

    @on(ApprovalBar.ApprovalSelected)
    def on_approval_selected(self, event: ApprovalBar.ApprovalSelected) -> None:
        bar = self.query_one(ApprovalBar)
        request_id = bar.request_id
        if request_id is None:
            return
        self.controller.resolve_approval(request_id, event.choice)
        self._interrupts.reset_interrupt_state()
        bar.hide_request()
        input_widget = self.query_one(Input)
        input_widget.disabled = False
        input_widget.focus()

    def on_key(self, event: Key) -> None:
        bar = self.query_one(ApprovalBar)
        if bar.display:
            if event.key == "up":
                bar.action_cursor_up()
            elif event.key == "down":
                bar.action_cursor_down()
            elif event.key == "enter":
                bar.action_confirm()
            elif event.key == "escape":
                bar.action_deny()
            event.stop()
            event.prevent_default()
            return

        question = self.query_one(PlanQuestionWidget)
        if question.display:
            if event.key == "up":
                question.action_cursor_up()
            elif event.key == "down":
                question.action_cursor_down()
            elif event.key == "enter":
                question.action_confirm()
            else:
                return
            event.stop()
            event.prevent_default()
            return

        popup = self._slash_popup()
        input_widget = self.query_one(Input)
        popup_visible = bool(popup is not None and popup.visible)
        if popup is not None and popup_visible and self.focused is input_widget:
            if event.key == "down":
                popup.cursor_down()
            elif event.key == "up":
                popup.cursor_up()
            elif event.key == "escape":
                popup.hide_popup()
            elif event.key == "tab":
                trigger = popup.accept_selected_trigger()
                if trigger is not None:
                    input_widget.value = trigger
                    input_widget.cursor_position = len(trigger)
                    self._refresh_slash_popup(trigger)
            elif event.key == "enter":
                selected = popup.selected_item()
                if selected is not None:
                    input_widget.value = selected.trigger
                    self._close_slash_popup()
                    self._handle_slash_command(selected.trigger)
                    input_widget.focus()
            else:
                return
            event.stop()
            event.prevent_default()
            return

        transcript = self.query_one(VillaniTranscript)
        if event.key == "space":
            focused = self.focused
            if isinstance(focused, Input) and not focused.disabled:
                cursor = focused.cursor_position
                value = focused.value
                focused.value = f"{value[:cursor]} {value[cursor:]}"
                focused.cursor_position = cursor + 1
                event.stop()
                event.prevent_default()
            return
        if event.key == "home":
            self.set_follow_tail(False)
            transcript.scroll_home(animate=False)
        elif event.key == "pageup":
            self.set_follow_tail(False)
            transcript.scroll_page_up(animate=False)
        elif event.key == "pagedown":
            transcript.scroll_page_down(animate=False)
            if transcript.is_vertical_scroll_end:
                self.set_follow_tail(True)
        elif event.key == "end":
            self.set_follow_tail(True)
            transcript.scroll_end(animate=False)
