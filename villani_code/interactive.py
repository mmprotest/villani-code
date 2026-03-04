from __future__ import annotations

import asyncio
import json
import time
import uuid
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import Any

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
    tool_use_id: str | None
    tool: str
    tool_input: dict
    warnings: list[str]
    done: threading.Event
    decision: str = "deny"


@dataclass
class JobRecord:
    id: int
    title: str
    status: str


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[int, JobRecord] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._ids = count(1)

    def start_threaded_job(self, fn, title: str) -> int:
        job_id = next(self._ids)
        with self._lock:
            self._jobs[job_id] = JobRecord(id=job_id, title=title, status="running")

        def _run() -> None:
            try:
                fn()
                status = "completed"
            except Exception:
                status = "failed"
            with self._lock:
                if job_id in self._jobs:
                    self._jobs[job_id].status = status

        thread = threading.Thread(target=_run, daemon=True)
        self._threads[job_id] = thread
        thread.start()
        return job_id

    def cancel(self, job_id: int) -> None:
        with self._lock:
            if job_id in self._jobs and self._jobs[job_id].status == "running":
                self._jobs[job_id].status = "cancel_requested"


class SessionStore:
    def __init__(self, repo: Path, model: str, base_url: str, session_id: str | None = None) -> None:
        self.repo = repo
        self.model = model
        self.base_url = base_url
        self.session_id = session_id or str(uuid.uuid4())
        self.created_at = datetime.now(timezone.utc).isoformat()
        self._updated_at = self.created_at

    @property
    def session_path(self) -> Path:
        return self.repo / ".villani_code" / "sessions" / self.session_id / "session.json"

    def load(self) -> dict[str, Any]:
        data = json.loads(self.session_path.read_text(encoding="utf-8"))
        self.created_at = data.get("created_at", self.created_at)
        self._updated_at = data.get("updated_at", self._updated_at)
        return data

    def save(self, messages: list[dict[str, Any]]) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self._updated_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self._updated_at,
            "repo_path": str(self.repo),
            "model": self.model,
            "base_url": self.base_url,
            "messages": messages,
        }
        self.session_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class ApprovalManager:
    def __init__(self, enqueue):
        self._enqueue = enqueue
        self._pending: dict[int, ApprovalRequest] = {}
        self._ids = count(1)
        self._lock = threading.Lock()

    def request_approval(self, tool_name: str, tool_input: dict, tool_use_id: str | None = None) -> str:
        warnings = analyze_bash_command(str(tool_input.get("command", ""))) if tool_name == "Bash" else []
        req_id = next(self._ids)
        req = ApprovalRequest(id=req_id, tool_use_id=tool_use_id, tool=tool_name, tool_input=tool_input, warnings=warnings, done=threading.Event())
        with self._lock:
            self._pending[req_id] = req
        self._enqueue({"kind": "approval_request", "id": req_id, "tool_use_id": tool_use_id, "tool": tool_name, "input": tool_input, "warnings": warnings})
        req.done.wait()
        return req.decision

    def resolve(self, req_id: int, decision: str) -> None:
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
    def __init__(self, runner: Runner, repo: Path, base_url: str = "", resume: str | None = None):
        self.runner = runner
        self.repo = repo
        self.palette = CommandPalette()
        self.task_manager = TaskManager()
        self.status_bar = StatusBar()
        self.diff_viewer = DiffViewer(repo)
        self.settings = SettingsManager(repo)
        self.applied_settings = self.settings.load()
        self.runner.max_prompt_chars = self.applied_settings.max_prompt_chars
        self.theme = get_theme(self.applied_settings.theme)
        self.session_store = SessionStore(repo, runner.model, base_url, session_id=resume)
        self.resume_id = resume

    def run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        loop = asyncio.get_running_loop()
        ui_events: asyncio.Queue[dict] = asyncio.Queue()
        state = UIState(verbose_tool_output=self.applied_settings.verbose)
        session_data: dict[str, Any] | None = None
        skills = list(self.runner.skills.keys())
        completions = [i.trigger for i in self.palette.items] + ["/help", "/tasks", "/diff", "/settings", "/rewind", "/export", "/fork", "/exit"] + skills
        input_bar = InputBar(FuzzyWordCompleter(completions, WORD=True))
        transcript = TranscriptView(verbose_tool_output=state.verbose_tool_output)
        if self.resume_id:
            if self.session_store.session_path.exists():
                session_data = self.session_store.load()
            else:
                transcript.append_assistant_delta(f"Session {self.resume_id} not found")
        controller = Controller(self.runner, self.repo, state, transcript, self.task_manager)
        if session_data:
            restored_repo = Path(str(session_data.get("repo_path", "")))
            if restored_repo and restored_repo != self.repo and not restored_repo.exists():
                transcript.append_assistant_delta("Warning: original repo path does not exist, continuing in current directory.")
            controller.messages = list(session_data.get("messages", []))

        def enqueue_event(event: dict) -> None:
            loop.call_soon_threadsafe(ui_events.put_nowait, event)

        approval_manager = ApprovalManager(enqueue_event)
        approval_modal = ApprovalModal()

        def _on_stream(event: dict) -> None:
            kind = event.get("type")
            if kind == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                enqueue_event({"kind": "model_delta_text", "text": event.get("delta", {}).get("text", "")})
            elif kind in {"model_start", "model_stop", "usage_update"}:
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

        job_manager = JobManager()

        def _approval_allow() -> None:
            if state.pending_approval_id is not None:
                approval_manager.resolve(state.pending_approval_id, "allow_once")
                state.pending_approval_id = None
            state.active_modal = ActiveModal.NONE

        def _approval_deny() -> None:
            if state.pending_approval_id is not None:
                approval_manager.resolve(state.pending_approval_id, "deny")
                state.pending_approval_id = None
            state.active_modal = ActiveModal.NONE

        def _approval_background() -> None:
            if state.pending_approval_id is not None:
                approval_manager.resolve(state.pending_approval_id, "background_allow")
                job_id = job_manager.start_threaded_job(lambda: None, "Approved tool execution")
                enqueue_event({"kind": "job_backgrounded", "job_id": job_id})
                state.pending_approval_id = None
                state.active_modal = ActiveModal.NONE

        def _ctrl_c_pressed() -> None:
            now = time.monotonic()
            if state.ctrl_c_armed and now <= state.ctrl_c_armed_until:
                app.app.exit(result="interrupt")
                return
            state.ctrl_c_armed = True
            state.ctrl_c_armed_until = now + 2.0
            state.transient_message = "Pressing Ctrl+C again will exit Villani Code"

            async def _clear_ctrl_c() -> None:
                await asyncio.sleep(2.0)
                if time.monotonic() >= state.ctrl_c_armed_until:
                    state.ctrl_c_armed = False
                    state.transient_message = ""
                    app.invalidate()

            app.app.create_background_task(_clear_ctrl_c())
            app.invalidate()

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
            approve_approval=_approval_allow,
            deny_approval=_approval_deny,
            ctrl_c_pressed=_ctrl_c_pressed,
        )

        async def _handle_accept(value: str) -> None:
            if value.strip() == "/exit":
                app.app.exit()
                return
            await controller.handle_input(value)
            self.session_store.save(controller.messages or [])
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

        panel = HSplit([
            ConditionalContainer(task_panel.container, filter=show_tasks),
            ConditionalContainer(diff_panel.container, filter=show_diff),
        ])

        app = TUIApp(
            state=state,
            transcript=transcript,
            input_bar=input_bar,
            status_bar=self.status_bar,
            key_bindings=kb,
            panel=panel,
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
                    self.status_bar.update(connected=True, last_heartbeat=datetime.now(timezone.utc))
                    transcript.flush_tool_summary()
                    transcript.clear_activity()
                    self.session_store.save(controller.messages or [])
                elif kind == "usage_update":
                    self.status_bar.update(
                        total_tokens=int(event.get("total_tokens", self.status_bar.snapshot.total_tokens)),
                        tokens_last_minute=int(event.get("input_tokens", 0)) + int(event.get("output_tokens", 0)),
                        last_input_tokens=int(event.get("input_tokens", 0)),
                        last_output_tokens=int(event.get("output_tokens", 0)),
                        cache_read_tokens=int(event.get("cache_read_input_tokens", 0)),
                    )
                elif kind == "tool_use":
                    transcript.append_tool_call(event.get("name", ""), event.get("input", {}))
                elif kind == "tool_start":
                    self.status_bar.update(active_tools=self.status_bar.snapshot.active_tools + 1, last_tool_name=event.get("name", "-"))
                    transcript.set_activity(event.get("name", "tool"), "Running...")
                elif kind in {"tool_end", "tool_result"}:
                    _append_tool_event(kind, event, transcript, self.status_bar)
                elif kind == "approval_request":
                    approval_modal.set_request(event["id"], event["tool"], event["input"], event.get("warnings", []))
                    state.pending_approval_id = int(event["id"])
                    state.active_modal = ActiveModal.APPROVAL
                elif kind == "job_backgrounded":
                    transcript.append_assistant_delta(f"Running in background... [job:{event.get('job_id', '?')}]" )
                if self.status_bar.refresher.should_refresh():
                    app.invalidate()

        consumer_task = asyncio.create_task(_consume_events())
        try:
            await app.run()
        finally:
            consumer_task.cancel()
            self.session_store.save(controller.messages or [])
            print("Resume this session with:")
            print(f"villani-code interactive --resume {self.session_store.session_id}")



def _append_tool_event(kind: str, event: dict[str, Any], transcript: TranscriptView, status_bar: StatusBar) -> None:
    if kind != "tool_end":
        return
    full = str(event.get("result", {}).get("content", ""))
    out_id = event.get("id", "")
    transcript.store_full_output(out_id, full)
    transcript.append_tool_result(event.get("name", ""), full[:240], out_id)
    status_bar.update(active_tools=max(0, status_bar.snapshot.active_tools - 1), last_tool_name=event.get("name", "-"))

def _changed_files(repo: Path) -> list[Path]:
    import subprocess

    proc = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True, check=False)
    files: list[Path] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        files.append(Path(line[3:]))
    return files
