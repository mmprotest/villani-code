from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from villani_code.tui.messages import ApprovalRequest, LogAppend, SpinnerState, StatusUpdate


class MessageSink(Protocol):
    def post_message(self, message: object) -> object: ...


@dataclass
class ApprovalWaiter:
    event: threading.Event
    choice: str | None = None


class RunnerController:
    def __init__(self, runner: Any, app: MessageSink) -> None:
        self.runner = runner
        self.app = app
        self._approval_waiters: dict[str, ApprovalWaiter] = {}
        self._allowlist: set[tuple[str, str]] = set()
        self._tool_calls: dict[str, tuple[str, dict[str, Any]]] = {}
        self._assistant_stream_saw_text = False

        self.runner.print_stream = False
        self.runner.approval_callback = self.request_approval
        self.runner.event_callback = self.on_runner_event

    def run_prompt(self, text: str) -> None:
        threading.Thread(target=self._run_prompt_worker, args=(text,), daemon=True).start()

    def _run_prompt_worker(self, text: str) -> None:
        self.app.post_message(LogAppend(f"you> {text}"))
        self.app.post_message(SpinnerState(True, "Thinking"))
        self.app.post_message(StatusUpdate("Thinking"))
        self._assistant_stream_saw_text = False
        result = self.runner.run(text)
        content = result.get("response", {}).get("content", [])
        response_text = "\n".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
        if response_text and not self._assistant_stream_saw_text:
            self.app.post_message(LogAppend(f"assistant> {response_text}"))
        self.app.post_message(SpinnerState(False, "Idle"))
        self.app.post_message(StatusUpdate("Idle"))

    def _target_for(self, tool_name: str, payload: dict[str, Any]) -> str:
        permissions = getattr(self.runner, "permissions", None)
        if permissions and hasattr(permissions, "target_for"):
            return str(permissions.target_for(tool_name, payload))
        return "<unknown>"

    def request_approval(self, tool_name: str, payload: dict[str, Any]) -> bool:
        target = self._target_for(tool_name, payload)
        key = (tool_name, target)
        if key in self._allowlist:
            return True

        request_id = str(uuid.uuid4())
        waiter = ApprovalWaiter(event=threading.Event())
        self._approval_waiters[request_id] = waiter
        self.app.post_message(LogAppend(f"⏸ Approval required: {tool_name} — {target}"))
        self.app.post_message(ApprovalRequest(f"Allow {tool_name} on {target}?", ["yes", "always", "no"], request_id))
        waiter.event.wait()
        choice = waiter.choice or "no"
        self._approval_waiters.pop(request_id, None)
        if choice == "always":
            self._allowlist.add(key)
        return choice in {"yes", "always"}

    def resolve_approval(self, request_id: str, choice: str) -> None:
        waiter = self._approval_waiters.get(request_id)
        if waiter is None:
            return
        waiter.choice = choice
        waiter.event.set()

    def on_runner_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "model_request_started":
            self.app.post_message(SpinnerState(True, "Thinking"))
            return
        if etype == "first_text_delta":
            self.app.post_message(SpinnerState(False, "Responding"))
            return
        if etype in {"tool_use", "tool_started"}:
            tool = str(event.get("name", ""))
            payload = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
            tool_use_id = str(event.get("tool_use_id", ""))
            if tool_use_id:
                self._tool_calls[tool_use_id] = (tool, payload)
            self.app.post_message(LogAppend(f"▶ Using tool: {tool}"))
            self.app.post_message(SpinnerState(True, f"Using tool: {tool}"))
            return
        if etype in {"tool_finished", "tool_result"}:
            self.app.post_message(SpinnerState(False, "Waiting"))
            return
        if etype == "stream_text":
            self._assistant_stream_saw_text = True
            self.app.post_message(LogAppend(str(event.get("text", ""))))
            return
        if etype == "command_policy":
            self.app.post_message(
                LogAppend(f"policy[{event.get('outcome')}] bash @ {event.get('cwd')}: {event.get('reason')}")
            )
