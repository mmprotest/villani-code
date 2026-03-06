from __future__ import annotations

from pathlib import Path
from typing import Any
from textual.timer import Timer

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key, MouseScrollDown, MouseScrollUp
from textual.widgets import Input, Log, Static

from villani_code.interrupts import InterruptController
from villani_code.tui.assets import LAUNCH_BANNER
from villani_code.tui.controller import RunnerController
from villani_code.tui.messages import ApprovalRequest, LogAppend, SpinnerState, StatusUpdate
from villani_code.tui.widgets.approval import ApprovalBar
from villani_code.tui.widgets.status import StatusBarWidget


class VillaniLog(Log):
    def _scroll_step(self, event: MouseScrollUp | MouseScrollDown) -> int:
        base = 12
        if getattr(event, "shift", False):
            return base * 3
        if getattr(event, "ctrl", False) or getattr(event, "control", False):
            return base * 8
        return base

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
    BINDINGS = [Binding("ctrl+c", "interrupt_or_quit", show=False, priority=True)]

    def __init__(self, runner: Any, repo: Path, villani_mode: bool = False, villani_objective: str | None = None) -> None:
        super().__init__()
        self.runner = runner
        self.repo = repo
        self.follow_tail = True
        self.follow_paused = False
        self._ai_streaming = False
        self._ai_started = False
        self._stream_buffer = ""
        self._stream_flush_timer: Timer | None = None
        self._interrupts = InterruptController()
        self.villani_mode = villani_mode
        self.villani_objective = villani_objective
        self.controller = RunnerController(runner, self)

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield VillaniLog(id="log")
            yield ApprovalBar()
            yield StatusBarWidget(id="status")
            with Horizontal(id="input-row"):
                yield Static("🤖 Villani Code >", id="input-prompt")
                yield Input(id="input")

    def on_mount(self) -> None:
        log = self.query_one(VillaniLog)
        for line in LAUNCH_BANNER.splitlines():
            log.write_line(line)
        log.write_line(f"Model: {getattr(self.runner, 'model', 'unknown')}")
        if self.villani_mode:
            objective = self.villani_objective or "inspect and improve this repository autonomously"
            log.write_line(f"Villani mode active: {objective}")
            self.query_one(StatusBarWidget).set_status("scanning repo")
            input_widget = self.query_one(Input)
            input_widget.disabled = True
            self.controller.run_villani_mode()
        else:
            log.write_line("Ready. Type /help for commands.")
            self.query_one(Input).focus()
        self.query_one(StatusBarWidget).set_follow_mode(self.follow_tail)

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text == "/exit":
            self.exit()
            return
        self._interrupts.reset_interrupt_state()
        self.controller.run_prompt(text)

    def action_interrupt_or_quit(self) -> None:
        action = self._interrupts.register_interrupt()
        if action == "exit":
            self.exit()
            return
        self.post_message(StatusUpdate("Interrupted current session. Press Ctrl+C again to exit Villani Code."))

    def _end_ai_stream_if_open(self, log: VillaniLog) -> None:
        self._flush_stream_buffer(log)
        if self._ai_streaming:
            log.write("\n", scroll_end=self.follow_tail)
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
        self._flush_stream_buffer(self.query_one(VillaniLog))

    def _flush_stream_buffer(self, log: VillaniLog) -> None:
        if not self._stream_buffer:
            return
        parts = self._stream_buffer.split("\n")
        log.write(parts[0], scroll_end=self.follow_tail)
        for line in parts[1:]:
            log.write_line(line, scroll_end=self.follow_tail)
        self._stream_buffer = ""

    def _start_ai_boundary(self, log: VillaniLog) -> None:
        if self._ai_started:
            return
        self._ai_started = True

    def on_log_append(self, message: LogAppend) -> None:
        log = self.query_one(VillaniLog)
        text = message.text
        kind = message.kind

        if kind in {"user", "meta"}:
            self._end_ai_stream_if_open(log)
            self._ai_started = False
            log.write(f"{text}\n", scroll_end=self.follow_tail)
            return

        if kind == "ai":
            self._end_ai_stream_if_open(log)
            self._start_ai_boundary(log)
            for line in text.rstrip("\n").split("\n"):
                log.write_line(line, scroll_end=self.follow_tail)
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
        log.write(f"{text}\n", scroll_end=self.follow_tail)

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
            self.query_one(VillaniLog).scroll_home(animate=False)
        elif event.key == "pageup":
            self.set_follow_tail(False)
            self.query_one(VillaniLog).scroll_page_up(animate=False)
        elif event.key == "pagedown":
            self.query_one(VillaniLog).scroll_page_down(animate=False)
            if self.query_one(VillaniLog).is_vertical_scroll_end:
                self.set_follow_tail(True)
        elif event.key == "end":
            self.set_follow_tail(True)
            self.query_one(VillaniLog).scroll_end(animate=False)
