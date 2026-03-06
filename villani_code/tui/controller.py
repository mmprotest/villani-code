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
        self._assistant_stream_saw_text = False

        self.runner.print_stream = False
        self.runner.approval_callback = self.request_approval
        self.runner.event_callback = self.on_runner_event

    def run_prompt(self, text: str) -> None:
        threading.Thread(target=self._run_prompt_worker, args=(text,), daemon=True).start()

    def run_villani_mode(self) -> None:
        threading.Thread(target=self._run_villani_mode_worker, daemon=True).start()


    def _run_villani_mode_worker(self) -> None:
        self.app.post_message(LogAppend("[villani-mode] Autonomous repo improvement started.", kind="meta"))
        self.app.post_message(SpinnerState(True, None))
        self.app.post_message(StatusUpdate("scanning repo"))
        result = self.runner.run_villani_mode()
        content = result.get("response", {}).get("content", [])
        response_text = "\n".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
        if response_text:
            self.app.post_message(LogAppend(response_text, kind="ai"))
        self.app.post_message(SpinnerState(False, "villani mode done"))
        self.app.post_message(StatusUpdate("summarizing"))

    def _run_prompt_worker(self, text: str) -> None:
        self.app.post_message(LogAppend(f"> {text}", kind="user"))
        self.app.post_message(SpinnerState(True, None))
        self.app.post_message(StatusUpdate("Thinking"))
        self._assistant_stream_saw_text = False
        result = self.runner.run(text)
        content = result.get("response", {}).get("content", [])
        response_text = "\n".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
        if response_text and not self._assistant_stream_saw_text:
            self.app.post_message(LogAppend(response_text, kind="ai"))
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
        self.app.post_message(ApprovalRequest(f"Allow {tool_name} on {target}?", ["yes", "always", "no"], request_id))
        self.app.post_message(StatusUpdate("Waiting for approval"))
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

    def _file_activity_line(self, tool: str, payload: dict[str, Any]) -> str | None:
        path = str(payload.get("file_path", "")).strip()
        if not path:
            return None
        if tool == "Read":
            return f"read  {path}"
        if tool == "Write":
            return f"write {path}"
        if tool == "Patch":
            return f"patch {path}"
        return None

    def on_runner_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "model_request_started":
            self.app.post_message(SpinnerState(True, None))
            self.app.post_message(StatusUpdate("Thinking"))
            return
        if etype == "first_text_delta":
            self.app.post_message(SpinnerState(True, None))
            self.app.post_message(StatusUpdate("Responding"))
            return
        if etype == "tool_started":
            tool = str(event.get("name", ""))
            payload = event.get("input", {}) if isinstance(event.get("input"), dict) else {}
            activity = self._file_activity_line(tool, payload)
            if activity:
                self.app.post_message(LogAppend(activity, kind="meta"))
                self.app.post_message(StatusUpdate(activity))
                self.app.post_message(SpinnerState(True, None))
            else:
                self.app.post_message(StatusUpdate("Working"))
                self.app.post_message(SpinnerState(True, None))
            return
        if etype in {"tool_finished", "tool_result"}:
            self.app.post_message(SpinnerState(False, None))
            self.app.post_message(StatusUpdate("Working"))
            return
        if etype == "autonomous_phase":
            phase = str(event.get("phase", "working"))
            self.app.post_message(StatusUpdate(phase))
            task = str(event.get("task", "")).strip()
            if task:
                self.app.post_message(LogAppend(f"[villani-mode] {phase}: {task}", kind="meta"))
            return
        if etype == "autonomous_scan":
            self.app.post_message(LogAppend(f"[villani-mode] scanned files={event.get('files_inspected', 0)}", kind="meta"))
            return
        if etype == "autonomous_candidates":
            tasks = event.get("tasks", [])
            self.app.post_message(LogAppend(f"[villani-mode] candidates: {', '.join(tasks)}", kind="meta"))
            return
        if etype == "takeover_dashboard":
            self.app.post_message(LogAppend(f"[takeover] repo assessment: {event.get('summary', '')}", kind="meta"))
            return
        if etype == "takeover_ranked":
            top = event.get("top", [])
            self.app.post_message(LogAppend(f"[takeover] ranked {event.get('count', 0)} opportunities; top: {', '.join(top)}", kind="meta"))
            return
        if etype == "takeover_wave":
            self.app.post_message(LogAppend(f"[takeover] executing wave {event.get('wave')}: {', '.join(event.get('selected', []))}", kind="meta"))
            return
        if etype == "takeover_wave_complete":
            self.app.post_message(LogAppend(f"[takeover] wave {event.get('wave')} complete, confidence {event.get('confidence')}, risk {event.get('risk')}, retired {event.get('retired')}", kind="meta"))
            return
        if etype == "failure_classified":
            self.app.post_message(LogAppend(f"[failure] classified as {event.get('category')}: {event.get('next_strategy')}", kind="meta"))
            return
        if etype == "verification_ran":
            self.app.post_message(LogAppend(f"[verification] status={event.get('status', 'unknown')} confidence={event.get('confidence', 'n/a')}", kind="meta"))
            return
        if etype == "confidence_risk":
            self.app.post_message(LogAppend(f"[risk] confidence {event.get('confidence')} risk {event.get('risk')} :: {event.get('summary')}", kind="meta"))
            return
        if etype == "stream_text":
            self._assistant_stream_saw_text = True
            self.app.post_message(LogAppend(str(event.get("text", "")), kind="stream"))
            return
