from __future__ import annotations

import asyncio
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ui.command_palette import CommandAction
from ui.task_board import TaskManager, TaskStatus

from villani_code.state import Runner
from villani_code.tui.app import TranscriptView
from villani_code.tui.state import UIState


class Controller:
    def __init__(self, runner: Runner, repo: Path, state: UIState, transcript: TranscriptView, task_manager: TaskManager) -> None:
        self.runner = runner
        self.repo = repo
        self.state = state
        self.transcript = transcript
        self.task_manager = task_manager
        self.token_events: deque[tuple[datetime, int]] = deque(maxlen=128)
        self.messages: list[dict[str, Any]] | None = None

    async def handle_input(self, text: str) -> None:
        value = text.strip()
        if not value:
            return
        if value.startswith("/"):
            await self.handle_command(value)
            return
        if value.startswith("!"):
            await self._run_bash(value[1:])
            return
        self.transcript.append_user(value)
        self.task_manager.create_task("model-call", "Model response")
        self.task_manager.update_status("model-call", TaskStatus.IN_PROGRESS, 0.2)
        result = await asyncio.to_thread(self.runner.run, value, self.messages)
        self.messages = result["messages"]
        self.task_manager.update_status("model-call", TaskStatus.COMPLETED, 1.0)
        for block in result["response"].get("content", []):
            if block.get("type") == "text":
                self.transcript.finalize_assistant_message(block.get("text", ""), result["response"].get("content", []))

    async def _run_bash(self, command: str) -> None:
        self.task_manager.record_event("ToolStart", command)
        self.state.active_tools += 1
        self.state.last_tool_name = "bash"
        self.transcript.append_tool_call("bash", {"command": command})
        proc = await asyncio.create_subprocess_shell(command, cwd=str(self.repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = (await proc.communicate())[0].decode("utf-8", errors="ignore")
        oid = f"out-{datetime.now(timezone.utc).timestamp()}"
        preview = output[:400] + ("\n... truncated ..." if len(output) > 400 else "")
        self.transcript.store_full_output(oid, output)
        self.transcript.append_tool_result("bash", preview, oid)
        self.state.active_tools = max(0, self.state.active_tools - 1)
        self.task_manager.record_event("ToolEnd", command)

    async def handle_command(self, line: str) -> None:
        if line in {"/", "/help"}:
            self.transcript.append_assistant_delta("/help /tasks /jobs /kill <pid> /diff /settings /rewind /export [name] /fork [name] /mcp /hooks /exit")
            return
        if line == "/tasks":
            self.state.show_tasks = not self.state.show_tasks
            return
        if line == "/diff":
            self.state.show_diff = not self.state.show_diff
            return
        if line == "/settings":
            self.state.last_error = "Settings opened"
            return
        if line.startswith("/rewind"):
            cps = self.runner.checkpoints.list()
            if cps:
                self.runner.checkpoints.rewind(cps[-1].id)
            return
        if line.startswith("/export"):
            parts = line.split()
            name = parts[1] if len(parts) > 1 else "session_export"
            src = self.repo / ".villani_code" / "sessions" / "last.json"
            if src.exists():
                (self.repo / f"{name}.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            return
        if line.startswith("/fork"):
            parts = line.split()
            name = parts[1] if len(parts) > 1 else "fork"
            src = self.repo / ".villani_code" / "sessions" / "last.json"
            if src.exists():
                (self.repo / ".villani_code" / "sessions" / f"{name}.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                subprocess.run(["git", "checkout", "-b", name], cwd=str(self.repo), capture_output=True)

    async def run_action(self, action: CommandAction) -> None:
        if action.target in {"tasks"}:
            await self.handle_command("/tasks")
        elif action.target in {"diff", "show_diff"}:
            await self.handle_command("/diff")
        elif action.target == "settings":
            await self.handle_command("/settings")
        elif action.target == "save_checkpoint":
            self.runner.checkpoints.create([], message_index=len(self.messages or []))
