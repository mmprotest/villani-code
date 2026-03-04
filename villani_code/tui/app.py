from __future__ import annotations

from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.events import Key, MouseScrollDown, MouseScrollUp
from textual.widgets import Input, Log

from villani_code.tui.assets import LAUNCH_BANNER
from villani_code.tui.controller import RunnerController
from villani_code.tui.messages import ApprovalRequest, LogAppend, SpinnerState, StatusUpdate
from villani_code.tui.widgets.approval import ApprovalBar
from villani_code.tui.widgets.status import StatusBarWidget


class VillaniLog(Log):
    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        app = self.app
        if isinstance(app, VillaniTUI):
            app.follow_tail = False
        self.scroll_relative(y=-6)
        event.stop()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self.scroll_relative(y=6)
        if self.is_vertical_scroll_end:
            app = self.app
            if isinstance(app, VillaniTUI):
                app.follow_tail = True
        event.stop()


class VillaniTUI(App[None]):
    CSS_PATH = "styles.tcss"

    def __init__(self, runner: Any, repo: Path) -> None:
        super().__init__()
        self.runner = runner
        self.repo = repo
        self.follow_tail = True
        self._streaming = False
        self.controller = RunnerController(runner, self)

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield VillaniLog(id="log")
            yield ApprovalBar()
            yield StatusBarWidget(id="status")
            yield Input(placeholder="🤖 Villani Code > ", id="input")

    def on_mount(self) -> None:
        log = self.query_one(VillaniLog)
        for line in LAUNCH_BANNER.splitlines():
            log.write_line(line)
        log.write_line(f"Model: {getattr(self.runner, 'model', 'unknown')}")
        log.write_line("Ready. Type /help for commands.")

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text == "/exit":
            self.exit()
            return
        self.controller.run_prompt(text)

    def _append_log_text(self, text: str) -> None:
        log = self.query_one(VillaniLog)
        if self._streaming and not text.startswith("assistant>"):
            parts = text.split("\n")
            log.write(parts[0], scroll_end=self.follow_tail)
            for part in parts[1:]:
                log.write_line(part, scroll_end=self.follow_tail)
            return
        if text.startswith("assistant> "):
            self._streaming = False
        log.write_line(text, scroll_end=self.follow_tail)

    def on_log_append(self, message: LogAppend) -> None:
        text = message.text
        if text and not text.startswith(("you>", "assistant>", "▶", "⏸", "policy[")):
            if not self._streaming:
                self.query_one(VillaniLog).write("assistant> ", scroll_end=self.follow_tail)
                self._streaming = True
        if text.startswith(("you>", "▶", "⏸", "policy[")):
            self._streaming = False
        self._append_log_text(text)

    def on_status_update(self, message: StatusUpdate) -> None:
        self.query_one(StatusBarWidget).set_status(message.text)

    def on_spinner_state(self, message: SpinnerState) -> None:
        self.query_one(StatusBarWidget).set_spinner(message.active, message.label)

    def on_approval_request(self, message: ApprovalRequest) -> None:
        self.query_one(ApprovalBar).show_request(message.prompt, message.request_id)
        self.query_one(Input).disabled = True

    @on(ApprovalBar.ApprovalSelected)
    def on_approval_selected(self, event: ApprovalBar.ApprovalSelected) -> None:
        bar = self.query_one(ApprovalBar)
        request_id = bar.request_id
        if request_id is None:
            return
        self.controller.resolve_approval(request_id, event.choice)
        self.post_message(LogAppend(f"approval> {event.choice}"))
        bar.hide_request()
        self.query_one(Input).disabled = False
        self.query_one(Input).focus()

    def on_key(self, event: Key) -> None:
        bar = self.query_one(ApprovalBar)
        if not bar.display:
            if event.key == "end":
                self.follow_tail = True
                self.query_one(VillaniLog).scroll_end(animate=False)
            return
        if event.key in {"left", "up"}:
            bar.move(-1)
            event.stop()
        elif event.key in {"right", "down", "tab"}:
            bar.move(1)
            event.stop()
        elif event.key == "enter":
            self.on_approval_selected(ApprovalBar.ApprovalSelected(bar.selected_choice()))
            event.stop()
        elif event.key == "escape":
            self.on_approval_selected(ApprovalBar.ApprovalSelected("no"))
            event.stop()
