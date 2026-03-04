from __future__ import annotations

import json
import subprocess
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Dialog, RadioList, TextArea
from rich.console import Console

from ui.command_palette import CommandAction, CommandPalette
from ui.diff_viewer import DiffViewer
from ui.settings import SettingsManager
from ui.status_bar import StatusBar
from ui.task_board import TaskManager, TaskStatus
from ui.themes import get_theme
from villani_code.status_controller import StatusController
from villani_code.state import Runner
from villani_code.tools import execute_tool


class InteractiveShell:
    MAX_LOG_HISTORY = 2000
    PREVIEW_MAX_LINES = 80
    PREVIEW_MAX_LINE_CHARS = 160

    LAUNCH_BANNER = (
        "+------------------------------------------------------------------------------+\n"
        "|  /\\_/\\                                                                       |\n"
        "| ( o.o )    _    _ _ _ _             _    _____          _                    |\n"
        "|  > ^ <    | |  | (_) | |           (_)  / ____|        | |                   |\n"
        "|           | |  | |_| | | __ _ _ __  _  | |     ___   __| | ___               |\n"
        "|           | |  | | | | |/ _` | '_ \\| | | |    / _ \\ / _` |/ _ \\              |\n"
        "|           | |__| | | | | (_| | | | | | | |___| (_) | (_| |  __/              |\n"
        "|            \\____/|_|_|_|\\__,_|_| |_|_|  \\_____\\___/ \\__,_|\\___/              |\n"
        "|                     (villani-fying your terminal, one token at a time)       |\n"
        "+------------------------------------------------------------------------------+"
    )

    def __init__(self, runner: Runner, repo: Path):
        self.runner = runner
        self.repo = repo
        self.console = Console()
        self.verbose_tools = False
        self.show_tasks = False
        self.focus_mode = False
        self.show_diff = False
        self.jobs: dict[int, subprocess.Popen] = {}

        self.palette = CommandPalette()
        self.task_manager = TaskManager()
        self.status_bar = StatusBar()
        self.diff_viewer = DiffViewer(repo)
        self.settings = SettingsManager(repo)
        self.applied_settings = self.settings.load()
        self.theme = get_theme(self.applied_settings.theme)
        self.token_events: deque[tuple[datetime, int]] = deque(maxlen=128)
        self._session_approval_allowlist: set[tuple[str, str]] = set()
        self._last_activity_line = ""
        self._files_read_recent: list[str] = []
        self._files_written_recent: list[str] = []
        self._max_recent_files = 5
        self._tool_calls: dict[str, tuple[str, dict[str, Any]]] = {}
        self._ui_actions: Queue[Callable[[], None]] = Queue()
        self._worker_thread: threading.Thread | None = None
        self._live_stream_text = ""
        self._running = False
        self._approval_request: dict[str, Any] | None = None
        self._approval_event: threading.Event | None = None
        self._palette_mode = False

        self.log_lines: deque[str] = deque(maxlen=self.MAX_LOG_HISTORY)
        self._log_text = ""
        self.log_area = TextArea(text="", read_only=True, scrollbar=True, focusable=False)
        self.stream_area = TextArea(text="", read_only=True, scrollbar=True, focusable=False)
        self.input_field = TextArea(
            multiline=False,
            prompt="🤖 Villani Code > ",
            style="class:input-field",
            accept_handler=self._on_input_accept,
        )
        self.status_control = FormattedTextControl(self._status_line_text)

        self.status_controller = StatusController(
            fps=10.0,
            render_to_stdout=False,
            on_update=self._invalidate_ui,
        )
        self.runner.print_stream = False
        self.runner.approval_callback = self._approval_prompt
        self.runner.event_callback = self._on_runner_event

        self.kb = self._build_keybindings()
        self.app = self._build_application()

    def run(self) -> None:
        self.console.print(self.LAUNCH_BANNER)
        self.console.print(f"[dim]Model: {self.runner.model}[/dim]")
        self._append_log("Ready. Type /help for commands.")
        self.app.run()
        self.status_controller.shutdown()

    def _build_application(self) -> Application:
        status_window = Window(content=self.status_control, height=1, always_hide_cursor=True)
        stream_container = ConditionalContainer(
            content=self.stream_area,
            filter=Condition(lambda: bool(self._live_stream_text)),
        )
        approval_container = ConditionalContainer(
            content=self._build_approval_dialog(),
            filter=Condition(lambda: self._approval_request is not None),
        )
        root = FloatContainer(
            content=HSplit(
                [
                    self.log_area,
                    stream_container,
                    status_window,
                    self.input_field,
                ]
            ),
            floats=[Float(content=approval_container)],
        )
        return Application(
            layout=Layout(root, focused_element=self.input_field),
            full_screen=True,
            key_bindings=self.kb,
            style=self.theme.prompt_toolkit_style,
        )

    def _build_approval_dialog(self) -> Dialog:
        radio = RadioList(
            [
                ("yes", "Yes (once)"),
                ("always", "Always for this target (session)"),
                ("no", "No"),
            ]
        )
        self._approval_radio = radio
        self._approval_preview = TextArea(
            text="",
            read_only=True,
            focusable=False,
            scrollbar=True,
            height=16,
            style="class:dialog.body",
        )
        body = HSplit([self._approval_preview, radio], padding=1)
        return Dialog(title="Approval required", body=body)

    def _build_keybindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-o")
        def _toggle_verbose(_):
            self.verbose_tools = not self.verbose_tools
            self._append_log(f"Verbose tool output: {self.verbose_tools}")

        @kb.add("c-t")
        def _toggle_tasks(_):
            self.show_tasks = not self.show_tasks
            self._append_log(f"Task panel: {self.show_tasks}")

        @kb.add("c-p")
        def _open_palette(_):
            self._palette_mode = True
            self._append_log("Palette mode: enter a command query and press Enter.")

        @kb.add("c-d")
        def _toggle_diff(_):
            self.show_diff = not self.show_diff
            self._append_log(f"Diff panel: {self.show_diff}")
            if self.show_diff:
                self._show_diff_panel()

        @kb.add("c-f")
        def _focus_mode(_):
            self.focus_mode = not self.focus_mode
            if self.focus_mode:
                self.show_tasks = False
                self.verbose_tools = False
            self._append_log(f"Focus mode: {self.focus_mode}")

        @kb.add("c-_")
        def _help(_):
            self._show_shortcuts_help()

        @kb.add("c-s")
        def _save_checkpoint(_):
            self._run_action(CommandAction(id="save", title="save", category="action", target="save_checkpoint"))

        @kb.add("c-c")
        def _cancel(event):
            if self._approval_request is not None:
                self._resolve_approval("no")
                return
            if self._running:
                self._append_log("Cancellation requested (best-effort).")
                for proc in list(self.jobs.values()):
                    proc.kill()
                return
            event.app.exit()

        @kb.add("enter")
        def _approve_on_enter(event):
            if self._approval_request is not None:
                self._resolve_approval(self._approval_radio.current_value)
                return
            event.app.current_buffer.validate_and_handle()

        return kb

    def _on_input_accept(self, buffer) -> bool:
        self._drain_ui_actions()
        text = buffer.text.strip()
        buffer.text = ""
        if not text or self._running:
            return True
        if self._palette_mode:
            self._palette_mode = False
            resolved = self.palette.resolve(text)
            if resolved:
                self._run_action(resolved)
            else:
                self._append_log(f"No palette match for: {text}")
            return True

        if text.startswith("!"):
            self._start_worker(lambda: self._run_bash_line(text[1:]))
            return True
        if text.startswith("/") and self._handle_slash(text):
            return True
        self._start_worker(lambda: self._run_model_turn(text))
        return True

    def _start_worker(self, fn: Callable[[], None]) -> None:
        self.input_field.read_only = True
        self._running = True
        self.status_controller.start_waiting("Thinking")

        def _wrapped() -> None:
            try:
                fn()
            finally:
                self._schedule_ui(self._finish_run)

        self._worker_thread = threading.Thread(target=_wrapped, daemon=True)
        self._worker_thread.start()

    def _finish_run(self) -> None:
        self._running = False
        self.input_field.read_only = False
        self.status_controller.update_phase("Idle")
        self._live_stream_text = ""
        self.stream_area.text = ""
        self._invalidate_ui()

    def _run_model_turn(self, text: str) -> None:
        self._schedule_ui(lambda: self._append_log(f"you> {text}"))
        self.task_manager.create_task("model-call", "Model response")
        self.task_manager.update_status("model-call", TaskStatus.IN_PROGRESS, progress=0.2)
        result = self.runner.run(text)
        tokens = self._extract_total_tokens(result.get("response", {}), text)
        self._schedule_ui(lambda: self._record_token_usage(tokens))
        self.task_manager.update_status("model-call", TaskStatus.COMPLETED, progress=1.0)
        response_text = "\n".join(
            block.get("text", "")
            for block in result.get("response", {}).get("content", [])
            if block.get("type") == "text"
        ).strip()
        if response_text:
            self._schedule_ui(lambda: self._append_log(f"assistant> {response_text}"))

    def _run_bash_line(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        self._schedule_ui(lambda: self._append_log(f"bash> {command}"))
        self.task_manager.record_event("ToolStart", command)
        self.status_controller.start_waiting("Using tool: bash", self._summarize_command(command))
        self.status_bar.update(active_tools=self.status_bar.snapshot.active_tools + 1, last_tool_name="bash")
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(self.repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.jobs[proc.pid] = proc
        out, _ = proc.communicate()
        self.jobs.pop(proc.pid, None)
        self.status_bar.update(active_tools=max(0, self.status_bar.snapshot.active_tools - 1))
        self.task_manager.record_event("ToolEnd", command)
        self._schedule_ui(lambda: self._render_tool_output("bash", out))

    def _append_log(self, text: str) -> bool:
        if not text:
            return False
        if text == self._last_activity_line:
            return False
        self._last_activity_line = text
        had_existing_lines = bool(self.log_lines)
        self.log_lines.append(text)
        if len(self.log_lines) == self.log_lines.maxlen and had_existing_lines:
            # Once we hit max history, rebuild from the bounded deque only when old lines are trimmed.
            self._log_text = "\n".join(self.log_lines)
        else:
            self._log_text += ("\n" if had_existing_lines else "") + text
        self.log_area.text = self._log_text
        self.log_area.buffer.cursor_position = len(self.log_area.text)
        self.task_manager.record_event("Activity", text)
        self._invalidate_ui()
        return True

    def _emit_activity(self, text: str) -> None:
        if not self._append_log(text):
            return
        printer = getattr(self.status_controller, "print_persistent", None)
        if callable(printer):
            printer(text)

    def _render_tool_output(self, tool_name: str, output: str) -> None:
        preview = output if len(output) < 600 else output[:600] + "\n[output truncated]"
        self._append_log(f"tool[{tool_name}]> {preview.strip()}")

    def _status_line_text(self) -> str:
        self._drain_ui_actions()
        return self._bottom_toolbar()

    def _bottom_toolbar(self) -> str:
        width = 120
        return self.status_bar.format(width) + f" | {self.status_controller.status_line()}"

    def _invalidate_ui(self) -> None:
        self.app.invalidate()

    def _schedule_ui(self, fn: Callable[[], None]) -> None:
        if not self.app.is_running:
            fn()
            return
        self._ui_actions.put(fn)
        self._invalidate_ui()

    def _drain_ui_actions(self) -> None:
        while not self._ui_actions.empty():
            self._ui_actions.get_nowait()()

    def _record_token_usage(self, tokens: int) -> None:
        now = datetime.now(timezone.utc)
        self.token_events.append((now, tokens))
        cutoff = now.timestamp() - 60
        rate = sum(t for at, t in self.token_events if at.timestamp() >= cutoff)
        self.status_bar.update(total_tokens=self.status_bar.snapshot.total_tokens + tokens, tokens_last_minute=rate)

    def _extract_total_tokens(self, response: dict[str, Any], prompt_text: str) -> int:
        usage = response.get("usage")
        if isinstance(usage, dict):
            if "input_tokens" in usage or "output_tokens" in usage:
                return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
            for key in ("total_tokens", "tokens"):
                if key in usage:
                    return int(usage.get(key, 0))
        return len(prompt_text.split()) + 20

    def _approval_prompt(self, tool_name: str, payload: dict[str, Any]) -> bool:
        self.status_controller.suspend()
        permissions = getattr(self.runner, "permissions", None)
        target = permissions.target_for(tool_name, payload) if permissions else "<unknown>"
        key = (tool_name, target)
        if key in self._session_approval_allowlist:
            return True
        if not self.app.is_running:
            choice = self._approval_choice_dialog(tool_name, target, payload)
            if choice == "always":
                self._session_approval_allowlist.add(key)
                self.status_controller.start_waiting(f"Using tool: {tool_name}", self._detail_for_tool(tool_name, payload))
                return True
            if choice == "yes":
                self.status_controller.start_waiting(f"Using tool: {tool_name}", self._detail_for_tool(tool_name, payload))
                return True
            self.status_controller.update_phase("Approval denied", tool_name)
            return False
        event = threading.Event()
        request = {"tool": tool_name, "target": target, "payload": payload, "event": event, "choice": "no"}
        self._schedule_ui(lambda: self._open_approval_modal(request))
        event.wait()
        choice = str(request["choice"])
        if choice == "always":
            self._session_approval_allowlist.add(key)
            return True
        return choice == "yes"

    def _approval_choice_dialog(self, _tool_name: str, _target: str, _payload: dict[str, Any]) -> str | None:
        return "no"

    def _open_approval_modal(self, request: dict[str, Any]) -> None:
        self._approval_request = request
        self._approval_event = request["event"]
        tool = request["tool"]
        target = request["target"]
        self._approval_preview.text = self._format_approval_preview(tool, target, request.get("payload", {}))
        self._append_log(f"approval> {tool} target={target}")

    def _format_approval_preview(self, tool_name: str, target: str, payload: dict[str, Any]) -> str:
        payload = payload if isinstance(payload, dict) else {"payload": payload}
        header = [f"tool: {tool_name}", f"target: {target}"]
        hint = self._approval_payload_hint(payload)
        if hint:
            header.append(hint)
        body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        return "\n".join(header + ["", "payload:", self._truncate_preview_text(body)])

    def _approval_payload_hint(self, payload: dict[str, Any]) -> str:
        hints: list[str] = []
        if "path" in payload:
            hints.append(f"path: {payload.get('path')}")
        if "diff" in payload:
            diff_lines = len(str(payload.get("diff", "")).splitlines())
            hints.append(f"diff: {diff_lines} lines (use /diff to inspect)")
        if "content" in payload:
            hints.append(f"content: {len(str(payload.get('content', '')))} chars")
        return " | ".join(hints)

    def _truncate_preview_text(self, text: str) -> str:
        lines = text.splitlines()
        clipped_lines = [line[: self.PREVIEW_MAX_LINE_CHARS] for line in lines[: self.PREVIEW_MAX_LINES]]
        was_truncated = len(lines) > self.PREVIEW_MAX_LINES or any(
            len(line) > self.PREVIEW_MAX_LINE_CHARS for line in lines[: self.PREVIEW_MAX_LINES]
        )
        if was_truncated:
            clipped_lines.append("... (truncated)")
        return "\n".join(clipped_lines)

    def _resolve_approval(self, choice: str) -> None:
        if not self._approval_request or not self._approval_event:
            return
        self._approval_request["choice"] = choice
        self._approval_request = None
        self._approval_event.set()
        self._approval_event = None
        self._invalidate_ui()

    def _run_action(self, action: CommandAction) -> None:
        if action.target == "save_checkpoint":
            self.runner.checkpoints.create([], message_index=0)
            self._append_log("Checkpoint created")
        elif action.target == "toggle_verbose":
            self.verbose_tools = not self.verbose_tools
            self._append_log(f"Verbose tool output: {self.verbose_tools}")
        elif action.target in {"show_diff", "diff"}:
            self._show_diff_panel()
        elif action.target == "tasks":
            self.show_tasks = not self.show_tasks
            if self.show_tasks:
                self._show_tasks_panel()
        elif action.target == "settings":
            self._append_log("Settings load from ~/.villani/settings.json and .villani/settings.json")
        elif action.target == "help":
            self._show_shortcuts_help()

    def _show_shortcuts_help(self) -> None:
        self._append_log("Ctrl+P palette | Ctrl+D diff | Ctrl+F focus | Ctrl+O verbose | Ctrl+T tasks | Ctrl+/ help")

    def _show_tasks_panel(self) -> None:
        self._append_log("Task Board:")
        for task in self.task_manager.tasks.values():
            self._append_log(f"- {task.title}: {task.status.value} ({int(task.progress * 100)}%)")

    def _show_diff_panel(self) -> None:
        text = self.diff_viewer.load_diff()
        files = self.diff_viewer.parse(text)
        for dfile in files:
            for hunk in dfile.hunks:
                self.diff_viewer.fold_hunk(hunk)
        rendered = self.diff_viewer.render_plain(files) or "No diff"
        self._append_log("Diff:\n" + rendered)

    def _handle_slash(self, line: str) -> bool:
        if line in {"/", "/help"}:
            self._append_log("/help /tasks /jobs /kill <pid> /diff /settings /rewind /export [name] /fork [name] /propose /edits /show <id> /apply <id> /reject <id> /mcp /hooks /exit")
            return True
        if line == "/exit":
            self.app.exit()
            return True
        if line == "/tasks":
            self._run_action(CommandAction(id="/tasks", title="tasks", category="command", target="tasks"))
            return True
        if line == "/settings":
            self._run_action(CommandAction(id="/settings", title="settings", category="command", target="settings"))
            return True
        if line == "/diff":
            self._run_action(CommandAction(id="/diff", title="diff", category="command", target="diff"))
            return True
        if line.startswith("/rewind"):
            cps = self.runner.checkpoints.list()
            if not cps:
                self._append_log("No checkpoints")
                return True
            cp = cps[-1]
            self.runner.checkpoints.rewind(cp.id)
            self._append_log(f"Rewound to {cp.id}")
            return True
        if line.startswith("/export"):
            parts = line.split()
            name = parts[1] if len(parts) > 1 else "session_export"
            src = self.repo / ".villani_code" / "sessions" / "last.json"
            if src.exists():
                (self.repo / f"{name}.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                self._append_log(f"Exported {name}.json")
            return True
        if line.startswith("/fork"):
            parts = line.split()
            name = parts[1] if len(parts) > 1 else "fork"
            src = self.repo / ".villani_code" / "sessions" / "last.json"
            if src.exists():
                (self.repo / ".villani_code" / "sessions" / f"{name}.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                subprocess.run(["git", "checkout", "-b", name], cwd=str(self.repo), capture_output=True)
                self._append_log(f"Forked session as {name}")
            return True
        if line.startswith("/propose"):
            self.runner.capture_next_diff_proposal = True
            self._append_log("Will capture the next assistant diff as a proposal.")
            return True
        if line == "/edits":
            edits = self.runner.proposals.list()
            if not edits:
                self._append_log("No edit proposals.")
                return True
            for edit in edits:
                self._append_log(f"{edit.id} [{edit.status}] {edit.summary}")
            return True
        if line.startswith("/show "):
            edit_id = line.split(maxsplit=1)[1]
            edit = self.runner.proposals.get(edit_id)
            if not edit:
                self._append_log(f"Unknown proposal: {edit_id}")
                return True
            self._append_log(edit.diff)
            return True
        if line.startswith("/apply "):
            edit_id = line.split(maxsplit=1)[1]
            edit = self.runner.proposals.get(edit_id)
            if not edit:
                self._append_log(f"Unknown proposal: {edit_id}")
                return True
            result = execute_tool("Patch", {"unified_diff": edit.diff}, self.repo, unsafe=self.runner.unsafe)
            if result.get("is_error"):
                self._append_log(f"Apply failed: {result.get('content')}")
            else:
                edit.status = "applied"
                self._append_log(f"Applied proposal {edit.id}")
            return True
        if line.startswith("/reject "):
            edit_id = line.split(maxsplit=1)[1]
            if self.runner.proposals.reject(edit_id):
                self._append_log(f"Rejected proposal {edit_id}")
            else:
                self._append_log(f"Unknown proposal: {edit_id}")
            return True
        if line == "/jobs":
            for pid, proc in self.jobs.items():
                self._append_log(f"{pid} running={proc.poll() is None}")
            return True
        if line.startswith("/kill "):
            pid = int(line.split()[1])
            if pid in self.jobs:
                self.jobs[pid].kill()
            return True
        return False

    def _on_runner_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "model_request_started":
            self._schedule_ui(lambda: self._append_log("🤔 Thinking…"))
            self.status_controller.start_waiting("Thinking", "")
            return
        if etype == "first_text_delta":
            self.status_controller.stop_spinner("Responding", "")
            return
        if etype in {"tool_use", "tool_started"}:
            tool_name = str(event.get("name", ""))
            tool_input = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
            tool_use_id = str(event.get("tool_use_id", ""))
            if tool_use_id:
                self._tool_calls[tool_use_id] = (tool_name, tool_input)
            detail = self._detail_for_tool(tool_name, tool_input)
            msg = f"▶ Tool: {tool_name}" + (f" — {detail}" if detail else "")
            if tool_name == "Read" and self._path_for_tool(tool_name, tool_input):
                self._note_file_read(self._path_for_tool(tool_name, tool_input))
            elif tool_name == "Write" and self._path_for_tool(tool_name, tool_input):
                self._emit_activity(f"✍️ Writing: {self._path_for_tool(tool_name, tool_input)}")
            elif tool_name == "Patch" and self._path_for_tool(tool_name, tool_input):
                self._emit_activity(f"🩹 Patching: {self._path_for_tool(tool_name, tool_input)}")
            else:
                self._schedule_ui(lambda m=msg: self._emit_activity(m.replace('▶ Tool', '▶ Using tool')))
            path = self._path_for_tool(tool_name, tool_input)
            self.status_controller.start_waiting(f"Using tool: {tool_name}", detail)
            self.status_bar.update(last_tool_name=tool_name)
            return
        if etype == "approval_required":
            tool_name = str(event.get("name", ""))
            payload = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
            detail = self._detail_for_tool(tool_name, payload)
            self._schedule_ui(lambda: self._append_log(f"⏸ Approval required: {tool_name} — {detail}"))
            self.status_controller.update_phase(f"Approval required: {tool_name}", detail)
            return
        if etype in {"tool_finished", "tool_result"}:
            tool_name = str(event.get("name", ""))
            tool_use_id = str(event.get("tool_use_id", ""))
            if tool_use_id and tool_use_id in self._tool_calls:
                tool_name, tool_input = self._tool_calls[tool_use_id]
                if not event.get("is_error"):
                    path = self._path_for_tool(tool_name, tool_input)
                    if tool_name == "Write" and path:
                        self._note_file_written(path, "Write")
                    if tool_name == "Patch" and path:
                        self._note_file_written(path, "Patch")
            outcome = "ok" if not event.get("is_error") else "error"
            self._schedule_ui(lambda: self._emit_activity(f"✓ Tool finished: {tool_name} ({outcome})"))
            self.status_controller.stop_spinner("Waiting", "")
            return
        if etype == "stream_text":
            text = str(event.get("text", ""))
            if text:
                self._schedule_ui(lambda t=text: self._update_stream_text(t))
            return
        if etype == "command_policy":
            line = f"policy[{event.get('outcome')}] bash @ {event.get('cwd')}: {event.get('reason')}"
            self._schedule_ui(lambda: self._append_log(line))
            return
        if etype == "edit_proposed":
            proposal_id = str(event.get("proposal_id", ""))
            summary = str(event.get("summary", ""))
            self._schedule_ui(lambda: self._append_log(f"✎ Edit proposed: {proposal_id} — {summary}"))

    def _update_stream_text(self, text: str) -> None:
        self._live_stream_text += text
        self.stream_area.text = self._live_stream_text
        self.stream_area.buffer.cursor_position = len(self.stream_area.text)
        self._invalidate_ui()

    def _note_file_read(self, path: str) -> None:
        if not path:
            return
        self._files_read_recent.append(path)
        self._files_read_recent = self._files_read_recent[-self._max_recent_files :]
        self._emit_activity(f"📖 Read: {path}")

    def _note_file_written(self, path: str, kind: str = "Write") -> None:
        if not path:
            return
        self._files_written_recent.append(path)
        self._files_written_recent = self._files_written_recent[-self._max_recent_files :]
        icon = "💾" if kind == "Write" else "🩹"
        verb = "Wrote" if kind == "Write" else "Patched"
        self._emit_activity(f"{icon} {verb}: {path}")

    def _path_for_tool(self, tool_name: str, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        if payload.get("file_path"):
            return str(payload.get("file_path"))
        if tool_name == "Patch":
            return self._path_from_unified_diff(str(payload.get("unified_diff", "")))
        return ""

    def _detail_for_tool(self, tool_name: str, payload: dict[str, Any]) -> str:
        if tool_name == "Read" and payload.get("file_path"):
            return f"Reading: {payload.get('file_path')}"
        if tool_name == "Write" and payload.get("file_path"):
            return f"Writing: {payload.get('file_path')}"
        if tool_name == "Patch" and payload.get("file_path"):
            return f"Patching: {payload.get('file_path')}"
        if tool_name == "Patch":
            patch_path = self._path_from_unified_diff(str(payload.get("unified_diff", "")))
            if patch_path:
                return f"Patching: {patch_path}"
        if tool_name.lower() == "bash":
            return self._summarize_bash_detail(payload)
        return ""

    def _summarize_command(self, command: str) -> str:
        cmd = command.strip().replace("\n", " ")
        summary = cmd[:80] + ("..." if len(cmd) > 80 else "")
        return f"cmd: {summary}"

    def _summarize_bash_detail(self, payload: dict[str, Any]) -> str:
        command = str(payload.get("command", "")).strip().replace("\n", " ")
        summary = command[:80] + ("..." if len(command) > 80 else "")
        cwd = payload.get("cwd")
        if cwd:
            return f"cmd: {summary} | cwd: {cwd}"
        return f"cmd: {summary}"

    def _path_from_unified_diff(self, unified_diff: str) -> str:
        for line in unified_diff.splitlines():
            if line.startswith("+++ "):
                target = line[4:].strip().split("\t", 1)[0]
                if target.startswith("b/"):
                    target = target[2:]
                if target != "/dev/null":
                    return target
        for line in unified_diff.splitlines():
            if line.startswith("--- "):
                source = line[4:].strip().split("\t", 1)[0]
                if source.startswith("a/"):
                    source = source[2:]
                if source != "/dev/null":
                    return source
        return ""
