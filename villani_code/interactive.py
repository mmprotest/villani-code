from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame

from ui.command_palette import CommandAction, CommandPalette
from ui.diff_viewer import DiffViewer
from ui.settings import SettingsManager
from ui.status_bar import StatusBar
from ui.task_board import TaskManager
from ui.themes import get_theme
from villani_code.command_analysis import analyze_bash_command
from villani_code.state import Runner
from villani_code.tui.app import InputBar, TUIApp, TranscriptView
from villani_code.tui.controller import Controller
from villani_code.tui.keybindings import build_keybindings
from villani_code.tui.modals.help import build_help_modal
from villani_code.tui.modals.output_viewer import OutputViewerModal
from villani_code.tui.modals.palette import PaletteModal
from villani_code.tui.modals.settings import SettingsModal
from villani_code.tui.panels.diff_viewer_panel import DiffViewerPanel
from villani_code.tui.panels.task_board_panel import TaskBoardPanel
from villani_code.tui.state import ActiveModal, UIState


@dataclass
class ApprovalRequest:
    id: int
    tool: str
    tool_input: dict
    warnings: list[str]
    done: threading.Event
    decision: bool = False


class ApprovalManager:
    def __init__(self, enqueue):
        self._enqueue = enqueue
        self._pending: dict[int, ApprovalRequest] = {}
        self._ids = count(1)
        self._lock = threading.Lock()

    def request_approval(self, tool_name: str, tool_input: dict) -> bool:
        warnings = analyze_bash_command(str(tool_input.get("command", ""))) if tool_name == "Bash" else []
        req_id = next(self._ids)
        req = ApprovalRequest(id=req_id, tool=tool_name, tool_input=tool_input, warnings=warnings, done=threading.Event())
        with self._lock:
            self._pending[req_id] = req
        self._enqueue({"kind": "approval_request", "id": req_id, "tool": tool_name, "input": tool_input, "warnings": warnings})
        req.done.wait()
        return req.decision

    def resolve(self, req_id: int, decision: bool) -> None:
        with self._lock:
            req = self._pending.pop(req_id, None)
        if req is None:
            return
        req.decision = decision
        req.done.set()


class ApprovalModal:
    def __init__(self) -> None:
        self.request_id: int | None = None
        self.lines: list[str] = ["No pending approval"]
        self.container = Frame(Window(content=FormattedTextControl(self._render), height=10), title="Tool Approval")

    def set_request(self, request_id: int, tool: str, tool_input: dict, warnings: list[str]) -> None:
        self.request_id = request_id
        self.lines = [
            f"Tool: {tool}",
            f"Input: {tool_input}",
            "",
            "Warnings:",
            *([f"- {w}" for w in warnings] or ["- none"]),
            "",
            "Enter: Yes once | Esc: No | Ctrl+B: Run in background",
        ]

    def _render(self):
        return [("", "\n".join(self.lines))]


class InteractiveShell:
    def __init__(self, runner: Runner, repo: Path):
        self.runner = runner
        self.repo = repo
        self.palette = CommandPalette()
        self.task_manager = TaskManager()
        self.status_bar = StatusBar()
        self.diff_viewer = DiffViewer(repo)
        self.settings = SettingsManager(repo)
        self.applied_settings = self.settings.load()
        self.theme = get_theme(self.applied_settings.theme)

    def run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        loop = asyncio.get_running_loop()
        ui_events: asyncio.Queue[dict] = asyncio.Queue()
        state = UIState(verbose_tool_output=self.applied_settings.verbose)
        skills = list(self.runner.skills.keys())
        completions = [i.trigger for i in self.palette.items] + ["/help", "/tasks", "/diff", "/settings", "/rewind", "/export", "/fork", "/exit"] + skills
        input_bar = InputBar(FuzzyWordCompleter(completions, WORD=True))
        transcript = TranscriptView(verbose_tool_output=state.verbose_tool_output)
        controller = Controller(self.runner, self.repo, state, transcript, self.task_manager)

        def enqueue_event(event: dict) -> None:
            loop.call_soon_threadsafe(ui_events.put_nowait, event)

        approval_manager = ApprovalManager(enqueue_event)
        approval_modal = ApprovalModal()

        def _on_stream(event: dict) -> None:
            kind = event.get("type")
            if kind == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                enqueue_event({"kind": "model_delta_text", "text": event.get("delta", {}).get("text", "")})
            elif kind in {"model_start", "model_stop"}:
                enqueue_event({"kind": kind, **event})

        def _on_tool_event(kind: str, payload: dict) -> None:
            enqueue_event({"kind": kind, **payload})

        self.runner.interactive_mode = True
        self.runner.stream_event_handler = _on_stream
        self.runner.tool_event_handler = _on_tool_event
        self.runner.approval_callback = approval_manager.request_approval

        task_panel = TaskBoardPanel(self.task_manager)
        diff_panel = DiffViewerPanel(self.repo)
        diff_panel.load()

        async def _run_action(action: CommandAction) -> None:
            await controller.run_action(action)
            state.active_modal = ActiveModal.NONE

        palette_modal = PaletteModal(self.palette, lambda action: asyncio.create_task(_run_action(action)))
        settings_modal = SettingsModal(self.applied_settings, lambda verbose, _auto: setattr(state, "verbose_tool_output", verbose))
        help_modal = build_help_modal()
        output_modal = OutputViewerModal()

        def _schedule(name: str) -> None:
            if name == "checkpoint":
                files = _changed_files(self.repo)
                message_index = len(controller.messages or [])
                self.runner.checkpoints.create(files, message_index=message_index)

        def _open_output() -> None:
            state.active_modal = ActiveModal.OUTPUT
            output_modal.set_text(transcript.get_full_output(state.output_view_id))

        def _close_modal() -> None:
            app.app.layout.focus(input_bar.textarea)

        def _focus_palette() -> None:
            app.app.layout.focus(palette_modal.focus_target())

        def _approval_background() -> None:
            if approval_modal.request_id is not None:
                approval_manager.resolve(approval_modal.request_id, True)
                state.active_modal = ActiveModal.NONE

        kb = build_keybindings(
            state,
            _schedule,
            _open_output,
            transcript.toggle_selected_fold,
            palette_move=palette_modal.move,
            palette_submit=palette_modal.submit,
            focus_palette=_focus_palette,
            close_modal=_close_modal,
            open_approval_background=_approval_background,
        )

        async def _handle_accept(value: str) -> None:
            if value.strip() == "/exit":
                app.app.exit()
                return
            await controller.handle_input(value)
            app.invalidate()

        def _accept(_buff) -> bool:
            value = input_bar.textarea.text
            input_bar.textarea.buffer.reset()
            app.app.create_background_task(_handle_accept(value))
            return False

        input_bar.textarea.accept_handler = _accept

        @Condition
        def show_tasks() -> bool:
            return state.show_tasks

        @Condition
        def show_diff() -> bool:
            return state.show_diff

        right_panel = HSplit([
            ConditionalContainer(task_panel.container, filter=show_tasks),
            ConditionalContainer(diff_panel.container, filter=show_diff),
        ])
        bottom_panel = HSplit([
            ConditionalContainer(task_panel.container, filter=show_tasks),
            ConditionalContainer(diff_panel.container, filter=show_diff),
        ])

        app = TUIApp(
            state=state,
            transcript=transcript,
            input_bar=input_bar,
            status_bar=self.status_bar,
            key_bindings=kb,
            right_panel=right_panel,
            bottom_panel=bottom_panel,
            palette_modal=palette_modal.container,
            help_modal=help_modal,
            settings_modal=settings_modal.container,
            output_modal=output_modal.container,
            approval_modal=approval_modal.container,
            style=self.theme.prompt_toolkit_style,
        )

        async def _consume_events() -> None:
            while True:
                event = await ui_events.get()
                kind = event.get("kind")
                if kind == "model_delta_text":
                    transcript.append_assistant_delta(event.get("text", ""))
                elif kind == "model_start":
                    self.status_bar.update(connected=True, last_heartbeat=datetime.now(timezone.utc))
                    transcript.set_activity("Model", "Running...")
                elif kind == "model_stop":
                    usage = event.get("usage", {}) or {}
                    total = int(usage.get("output_tokens", 0)) + int(usage.get("input_tokens", 0))
                    self.status_bar.update(connected=True, last_heartbeat=datetime.now(timezone.utc), total_tokens=self.status_bar.snapshot.total_tokens + total, tokens_last_minute=total)
                    transcript.flush_tool_summary()
                    transcript.clear_activity()
                elif kind == "tool_use":
                    transcript.append_tool_call(event.get("name", ""), event.get("input", {}))
                elif kind == "tool_start":
                    self.status_bar.update(active_tools=self.status_bar.snapshot.active_tools + 1, last_tool_name=event.get("name", "-"))
                    transcript.set_activity(event.get("name", "tool"), "Running...")
                elif kind in {"tool_end", "tool_result"}:
                    full = str(event.get("result", {}).get("content", ""))
                    out_id = event.get("id", "")
                    transcript.store_full_output(out_id, full)
                    transcript.append_tool_result(event.get("name", ""), full[:240], out_id)
                    self.status_bar.update(active_tools=max(0, self.status_bar.snapshot.active_tools - 1), last_tool_name=event.get("name", "-"))
                elif kind == "approval_request":
                    approval_modal.set_request(event["id"], event["tool"], event["input"], event.get("warnings", []))
                    state.active_modal = ActiveModal.APPROVAL
                if self.status_bar.refresher.should_refresh():
                    app.invalidate()

        consumer_task = asyncio.create_task(_consume_events())
        try:
            await app.run()
        finally:
            consumer_task.cancel()


def _changed_files(repo: Path) -> list[Path]:
    import subprocess

    proc = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True, check=False)
    files: list[Path] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        files.append(Path(line[3:]))
    return files
