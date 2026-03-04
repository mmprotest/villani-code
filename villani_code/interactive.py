from __future__ import annotations

import subprocess
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.widgets import Box, Frame, Label
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from ui.command_palette import CommandAction, CommandPalette
from ui.diff_viewer import DiffViewer
from ui.settings import SettingsManager
from ui.status_bar import StatusBar
from ui.task_board import TaskManager, TaskStatus
from ui.themes import get_theme
from villani_code.permissions import PermissionEngine
from villani_code.status_controller import StatusController
from villani_code.state import Runner


class InteractiveShell:
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
        self.lock = threading.Lock()

        self.palette = CommandPalette()
        self.task_manager = TaskManager()
        self.status_bar = StatusBar()
        self.diff_viewer = DiffViewer(repo)
        self.settings = SettingsManager(repo)
        self.applied_settings = self.settings.load()
        self.theme = get_theme(self.applied_settings.theme)
        self.token_events: deque[tuple[datetime, int]] = deque(maxlen=128)
        self.pending_action: CommandAction | None = None
        self._session_approval_allowlist: set[tuple[str, str]] = set()
        self.status_controller = StatusController(fps=10.0)
        self.runner.approval_callback = self._approval_prompt
        self.runner.event_callback = self._on_runner_event

    def run(self) -> None:
        self.console.print(self.LAUNCH_BANNER)
        kb = self._build_keybindings()
        completer = FuzzyWordCompleter([i.trigger for i in self.palette.items], WORD=True)
        session = PromptSession(
            "🤖 Villani Code > ",
            key_bindings=kb,
            completer=completer,
            history=InMemoryHistory(),
            bottom_toolbar=self._bottom_toolbar,
            style=self.theme.prompt_toolkit_style,
        )
        try:
            while True:
                self._poll_settings()
                self._execute_pending_action()
                try:
                    text = session.prompt()
                except EOFError:
                    return
                except KeyboardInterrupt:
                    self.console.print("[yellow]Cancelled[/yellow]")
                    self.status_controller.update_phase("Idle")
                    continue
                if not text.strip():
                    continue
                if text.strip().startswith("!"):
                    self._run_bash_line(text[1:])
                    continue
                if text.strip().startswith("/"):
                    if self._handle_slash(text.strip()):
                        continue
                self.task_manager.create_task("model-call", "Model response")
                self.task_manager.update_status("model-call", TaskStatus.IN_PROGRESS, progress=0.2)
                self.status_controller.start_waiting("Thinking")
                result = self.runner.run(text)
                tokens = self._extract_total_tokens(result.get("response", {}), text)
                self._record_token_usage(tokens)
                self.status_bar.update(connected=True, last_heartbeat=datetime.now(timezone.utc))
                self.task_manager.update_status("model-call", TaskStatus.COMPLETED, progress=1.0)
                self.status_controller.update_phase("Idle")
                for block in result["response"].get("content", []):
                    if block.get("type") == "text":
                        self._render_response(block.get("text", ""))
        finally:
            self.status_controller.shutdown()

    def _build_keybindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-o")
        def _toggle_verbose(_):
            self.verbose_tools = not self.verbose_tools
            self.console.print(f"[dim]Verbose tool output: {self.verbose_tools}[/dim]")

        @kb.add("c-t")
        def _toggle_tasks(_):
            self.show_tasks = not self.show_tasks
            self.console.print(f"[dim]Task panel: {self.show_tasks}[/dim]")

        @kb.add("c-p")
        def _open_palette(_):
            with self.lock:
                self.pending_action = CommandAction(id="palette", title="palette", category="overlay", target="palette")

        @kb.add("c-s")
        def _save_checkpoint(_):
            with self.lock:
                self.pending_action = CommandAction(id="save", title="save", category="action", target="save_checkpoint")

        @kb.add("c-d")
        def _toggle_diff(_):
            self.show_diff = not self.show_diff
            if self.show_diff:
                self._show_diff_panel()

        @kb.add("c-f")
        def _focus_mode(_):
            self.focus_mode = not self.focus_mode
            if self.focus_mode:
                self.show_tasks = False
                self.verbose_tools = False
            self.console.print(f"[dim]Focus mode: {self.focus_mode}[/dim]")

        @kb.add("c-_")
        def _help(_):
            self._show_shortcuts_help()

        return kb

    def _bottom_toolbar(self) -> str:
        width = self.console.size.width
        return self.status_bar.format(width)

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
        target = PermissionEngine._target_for(self.runner.permissions, tool_name, payload)
        key = (tool_name, target)
        if key in self._session_approval_allowlist:
            return True

        choice = self._approval_choice_dialog(tool_name, target, payload)
        if choice == "yes":
            self.status_controller.start_waiting(f"Using tool: {tool_name}", self._detail_for_tool(tool_name, payload))
            return True
        if choice == "always":
            self._session_approval_allowlist.add(key)
            self.status_controller.start_waiting(f"Using tool: {tool_name}", self._detail_for_tool(tool_name, payload))
            return True
        self.status_controller.update_phase("Approval denied", tool_name)
        return False

    def _approval_choice_dialog(self, tool_name: str, target: str, payload: dict[str, Any]) -> str | None:
        options = [
            ("yes", "Yes (once)"),
            ("always", "Always for this target (session)"),
            ("no", "No"),
        ]
        selected_index = 0

        def _options_text() -> list[tuple[str, str]]:
            fragments: list[tuple[str, str]] = []
            for idx, (_value, label) in enumerate(options):
                style = "fg:ansiblue bold" if idx == selected_index else ""
                fragments.append((style, f"{label}\n"))
            return fragments

        options_control = FormattedTextControl(_options_text)
        options_window = Window(content=options_control, height=D(min=3), always_hide_cursor=True)
        help_text = (
            f"Approval required: {tool_name}\n"
            f"Target: {target}\n\n"
            f"{self._format_payload_preview(payload)}\n\n"
            "Use ↑/↓ to move, Enter to confirm."
        )
        kb = KeyBindings()

        @kb.add("up")
        def _up(_event):
            nonlocal selected_index
            selected_index = (selected_index - 1) % len(options)

        @kb.add("down")
        def _down(_event):
            nonlocal selected_index
            selected_index = (selected_index + 1) % len(options)

        @kb.add("enter")
        def _accept(event):
            event.app.exit(result=options[selected_index][0])

        @kb.add("c-c")
        @kb.add("escape")
        def _cancel(event):
            event.app.exit(result=None)

        root = Box(
            body=HSplit(
                [
                    Label(text=help_text),
                    Frame(body=options_window, title="Select approval option"),
                ]
            ),
            padding=1,
        )
        app = Application(layout=Layout(root, focused_element=options_window), key_bindings=kb, full_screen=False)
        return app.run()

    def _format_payload_preview(self, payload: dict[str, Any]) -> str:
        prominent = [k for k in ("file_path", "command", "url") if payload.get(k)]
        lines = [f"  {key}: {self._truncate_preview(str(payload.get(key, '')))}" for key in prominent]
        raw = str(payload)
        if len(raw) > 500:
            raw = raw[:500] + "..."
        lines.append(f"  payload: {raw}")
        return "\n".join(lines)

    def _truncate_preview(self, value: str, max_len: int = 120) -> str:
        return value if len(value) <= max_len else value[:max_len] + "..."

    def _run_bash_line(self, command: str) -> None:
        self.task_manager.record_event("ToolStart", command)
        self.status_controller.start_waiting("Using tool: bash", self._summarize_command(command))
        self.status_bar.update(active_tools=self.status_bar.snapshot.active_tools + 1, last_tool_name="bash")
        proc = subprocess.Popen(command, shell=True, cwd=str(self.repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.jobs[proc.pid] = proc
        out, _ = proc.communicate()
        self._render_tool_output("bash", out)
        self.jobs.pop(proc.pid, None)
        self.status_bar.update(active_tools=max(0, self.status_bar.snapshot.active_tools - 1))
        self.task_manager.record_event("ToolEnd", command)
        self.status_controller.update_phase("Idle")

    def _render_tool_output(self, tool_name: str, output: str) -> None:
        preview = output if len(output) < 600 else output[:600] + "\n[output truncated, use /tasks details to expand]"
        table = Table(title=f"Tool Result: {tool_name}")
        table.add_column("Preview")
        table.add_row(preview)
        self.console.print(table)

    def _render_response(self, text: str) -> None:
        if len(text) > 1400:
            folded = text[:1000] + "\n\n```text\n... folded output ...\n```"
            self.console.print(Markdown(folded))
            return
        self.console.print(Markdown(text))

    def _execute_pending_action(self) -> None:
        with self.lock:
            action = self.pending_action
            self.pending_action = None
        if not action:
            return
        if action.target == "palette":
            palette_query = PromptSession("Command Palette > ").prompt()
            resolved = self.palette.resolve(palette_query)
            if resolved:
                self._run_action(resolved)
        else:
            self._run_action(action)

    def _run_action(self, action: CommandAction) -> None:
        if action.target == "save_checkpoint":
            self.runner.checkpoints.create([], message_index=0)
            self.console.print("[green]Checkpoint created[/green]")
            self.task_manager.record_event("CheckpointCreated", "quick save")
        elif action.target == "toggle_verbose":
            self.verbose_tools = not self.verbose_tools
        elif action.target in {"show_diff", "diff"}:
            self._show_diff_panel()
        elif action.target == "tasks":
            self.show_tasks = not self.show_tasks
            if self.show_tasks:
                self._show_tasks_panel()
        elif action.target == "settings":
            self.console.print(Panel.fit("Settings are loaded from ~/.villani/settings.json and .villani/settings.json"))
        elif action.target == "help":
            self._show_shortcuts_help()

    def _show_shortcuts_help(self) -> None:
        self.console.print(Panel.fit("Ctrl+P palette\nCtrl+S checkpoint\nCtrl+D diff\nCtrl+F focus\nCtrl+/ shortcuts\nCtrl+O verbose\nCtrl+T tasks"))

    def _show_tasks_panel(self) -> None:
        table = Table(title="Task Board")
        table.add_column("Task")
        table.add_column("Status")
        table.add_column("Progress")
        for task in self.task_manager.tasks.values():
            table.add_row(task.title, task.status.value, f"{int(task.progress*100)}%")
        self.console.print(table)

    def _show_diff_panel(self) -> None:
        text = self.diff_viewer.load_diff()
        files = self.diff_viewer.parse(text)
        for dfile in files:
            for hunk in dfile.hunks:
                self.diff_viewer.fold_hunk(hunk)
        self.console.print(self.diff_viewer.render_plain(files) or "No diff")
        self.task_manager.record_event("DiffViewed", "opened diff")

    def _poll_settings(self) -> None:
        settings = self.settings.reload_if_changed()
        if not settings:
            return
        self.applied_settings = settings
        self.theme = get_theme(settings.theme)
        self.verbose_tools = settings.verbose

    def _handle_slash(self, line: str) -> bool:
        if line in {"/", "/help"}:
            self.console.print("/help /tasks /jobs /kill <pid> /diff /settings /rewind /export [name] /fork [name] /mcp /hooks /exit")
            return True
        if line == "/exit":
            raise EOFError
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
                self.console.print("No checkpoints")
                return True
            cp = cps[-1]
            self.runner.checkpoints.rewind(cp.id)
            self.console.print(f"Rewound to {cp.id}")
            return True
        if line.startswith("/export"):
            parts = line.split()
            name = parts[1] if len(parts) > 1 else "session_export"
            src = self.repo / ".villani_code" / "sessions" / "last.json"
            if src.exists():
                txt = src.read_text(encoding="utf-8")
                (self.repo / f"{name}.json").write_text(txt, encoding="utf-8")
                self.console.print(f"Exported {name}.json")
            return True
        if line.startswith("/fork"):
            parts = line.split()
            name = parts[1] if len(parts) > 1 else "fork"
            src = self.repo / ".villani_code" / "sessions" / "last.json"
            if src.exists():
                (self.repo / ".villani_code" / "sessions" / f"{name}.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                subprocess.run(["git", "checkout", "-b", name], cwd=str(self.repo), capture_output=True)
                self.console.print(f"Forked session as {name}")
            return True
        if line == "/jobs":
            for pid, proc in self.jobs.items():
                self.console.print(f"{pid} running={proc.poll() is None}")
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
            self.status_controller.start_waiting("Thinking")
            return
        if etype == "first_text_delta":
            self.status_controller.stop_spinner("Streaming")
            return
        if etype == "tool_use":
            tool_name = str(event.get("name", ""))
            tool_input = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
            detail = self._detail_for_tool(tool_name, tool_input)
            self.status_controller.start_waiting(f"Using tool: {tool_name}", detail)
            self.status_controller.push_action(f"{tool_name}: {detail}" if detail else tool_name)
            return
        if etype == "approval_required":
            tool_name = str(event.get("name", ""))
            detail = self._detail_for_tool(tool_name, event.get("input", {}))
            self.status_controller.update_phase(f"Awaiting approval: {tool_name}", detail)
            return
        if etype == "tool_result":
            tool_name = str(event.get("name", ""))
            outcome = "ok" if not event.get("is_error") else "error"
            self.status_controller.push_action(f"{tool_name} finished ({outcome})")
            self.status_controller.start_waiting("Thinking")

    def _detail_for_tool(self, tool_name: str, payload: dict[str, Any]) -> str:
        payload = self._redact_payload(payload)
        if tool_name in {"Read", "Write", "Patch"} and payload.get("file_path"):
            prefix = "Editing" if tool_name in {"Write", "Patch"} else "Reading"
            return f"{prefix}: {payload.get('file_path')}"
        if tool_name in {"Grep", "Search", "Ls", "Glob"} and payload.get("path"):
            return f"Path: {payload.get('path')}"
        if tool_name.startswith("Git"):
            op = tool_name.replace("Git", "").lower() or "operation"
            return f"git {op} @ {self.repo}"
        if tool_name == "Bash":
            return self._summarize_command(str(payload.get("command", "")))
        if "url" in payload:
            return f"URL: {payload.get('url')}"
        for key in ("file", "target", "cwd"):
            if payload.get(key):
                return f"{key}: {payload.get(key)}"
        return ""

    def _summarize_command(self, command: str) -> str:
        cmd = command.strip().replace("\n", " ")
        summary = cmd[:80] + ("..." if len(cmd) > 80 else "")
        if "cd " in cmd:
            chunks = cmd.split("cd ", 1)[1].split(" ", 1)
            return f"cmd: {summary} | path: {chunks[0]}"
        return f"cmd: {summary}"

    def _redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            lowered = key.lower()
            if any(token in lowered for token in ("token", "key", "secret")):
                redacted[key] = "***"
            else:
                redacted[key] = value
        return redacted
