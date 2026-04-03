from __future__ import annotations

import copy
import inspect
import shlex
import threading
import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from villani_code.context_budget import ContextBudget
from villani_code.execution import ExecutionBudget
from villani_code.plan_session import PlanAnswer, PlanSessionResult
from villani_code.runtime_events import RuntimeEvent
from villani_code.tui.messages import ApprovalRequest, LogAppend, SpinnerState, StatusUpdate


class ControllerRunner(Protocol):
    print_stream: bool
    approval_callback: Any
    event_callback: Any
    permissions: Any

    def run(
        self,
        instruction: str,
        messages: list[dict[str, Any]] | None = None,
        execution_budget: ExecutionBudget | None = None,
        approved_plan: PlanSessionResult | None = None,
    ) -> dict[str, Any]: ...

    def plan(self, instruction: str, answers: list[PlanAnswer] | None = None) -> PlanSessionResult: ...

    def run_villani_mode(self) -> dict[str, Any]: ...


class ControllerAppHost(Protocol):
    def post_message(self, message: object) -> object: ...

    def call_from_thread(self, callback: Any, *args: Any, **kwargs: Any) -> Any: ...

    def apply_plan_result(self, result: PlanSessionResult, reset_answers: bool) -> None: ...

    def record_plan_answer(self, answer: PlanAnswer) -> None: ...

    def get_plan_instruction(self) -> str: ...

    def get_plan_answers(self) -> list[PlanAnswer]: ...

    def get_last_ready_plan(self) -> PlanSessionResult | None: ...

    def set_plan_stage(self, stage: str) -> None: ...


@dataclass
class ApprovalWaiter:
    event: threading.Event
    choice: str | None = None


class RunnerController:
    def __init__(self, runner: ControllerRunner, app: ControllerAppHost) -> None:
        self._validate_runner_contract(runner)
        self.runner = runner
        self.app = app
        self._approval_waiters: dict[str, ApprovalWaiter] = {}
        self._approval_scopes: set[str] = set()
        self._assistant_stream_saw_text = False
        self._suppress_assistant_stream_text = False
        self._session_messages: list[dict[str, Any]] | None = None
        self._session_context_budget = ContextBudget(max_chars=120_000, keep_last_turns=8)

        self.runner.print_stream = False
        self.runner.approval_callback = self.request_approval
        self.runner.event_callback = self.on_runner_event

    def _validate_runner_contract(self, runner: object) -> None:
        required_callables = ("run", "plan", "run_villani_mode")
        missing = [name for name in required_callables if not callable(getattr(runner, name, None))]
        if missing:
            raise TypeError(f"RunnerController runner is missing required method(s): {', '.join(missing)}")
        for attr in ("print_stream", "approval_callback", "event_callback", "permissions"):
            if not hasattr(runner, attr):
                raise TypeError(f"RunnerController runner is missing required attribute: {attr}")
        first_param = next(iter(inspect.signature(getattr(runner, "run")).parameters.values()), None)
        if first_param is None:
            raise TypeError("RunnerController runner.run must accept an instruction positional argument")

    def run_prompt(self, text: str) -> None:
        threading.Thread(target=self._run_prompt_worker, args=(text,), daemon=True).start()

    def run_plan_prompt(self, text: str) -> None:
        threading.Thread(target=self._run_plan_prompt_worker, args=(text,), daemon=True).start()

    def submit_plan_answer(self, answer: PlanAnswer) -> None:
        threading.Thread(target=self._submit_plan_answer_worker, args=(answer,), daemon=True).start()

    def replan(self) -> None:
        threading.Thread(target=self._replan_worker, daemon=True).start()

    def run_execute_plan(self) -> None:
        threading.Thread(target=self._run_execute_plan_worker, daemon=True).start()

    def run_villani_mode(self) -> None:
        threading.Thread(target=self._run_villani_mode_worker, daemon=True).start()

    def _runner_run(
        self,
        instruction: str,
        messages: list[dict[str, Any]] | None = None,
        execution_budget: ExecutionBudget | None = None,
        approved_plan: PlanSessionResult | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"messages": messages, "execution_budget": execution_budget}
        if approved_plan is not None:
            kwargs["approved_plan"] = approved_plan
        return self.runner.run(instruction, **kwargs)

    def _runner_plan(self, instruction: str, answers: list[PlanAnswer] | None = None) -> PlanSessionResult:
        return self.runner.plan(instruction, answers=answers)

    def _runner_run_villani_mode(self) -> dict[str, Any]:
        return self.runner.run_villani_mode()

    def reset_session_context(self) -> None:
        self._session_messages = None

    def fork_session_context(self) -> list[dict[str, Any]] | None:
        """Return a deep copy for future /fork session creation."""
        return copy.deepcopy(self._session_messages) if self._session_messages is not None else None

    def _messages_for_prompt(self, text: str) -> list[dict[str, Any]] | None:
        if self._session_messages is None:
            return None
        next_messages = copy.deepcopy(self._session_messages)
        next_messages.append({"role": "user", "content": [{"type": "text", "text": text}]})
        return self._session_context_budget.compact_session_messages(next_messages)

    def _ui_call(self, callback: Any, *args: Any, **kwargs: Any) -> Any:
        return self.app.call_from_thread(callback, *args, **kwargs)

    def _set_plan_stage(self, stage: str) -> None:
        callback = getattr(self.app, "set_plan_stage", None)
        if callable(callback):
            self._ui_call(callback, stage)

    def _run_villani_mode_worker(self) -> None:
        self.app.post_message(LogAppend("[villani-mode] Autonomous repo improvement started.", kind="meta"))
        self.app.post_message(SpinnerState(True, None))
        self.app.post_message(StatusUpdate("scanning repo"))
        try:
            result = self._runner_run_villani_mode()
            content = result.get("response", {}).get("content", [])
            response_text = "\n".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
            if response_text:
                self.app.post_message(LogAppend(response_text, kind="ai"))
            self.app.post_message(SpinnerState(False, "villani mode done"))
            self.app.post_message(StatusUpdate("summarizing"))
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            self.app.post_message(LogAppend(f"[villani-mode] ERROR {type(exc).__name__}: {exc}", kind="meta"))
            self.app.post_message(LogAppend(tb, kind="meta"))
            self.app.post_message(SpinnerState(False, "villani mode failed"))
            self.app.post_message(StatusUpdate("Idle"))

    def _run_prompt_worker(self, text: str) -> None:
        self.app.post_message(LogAppend(f"> {text}", kind="user"))
        self.app.post_message(SpinnerState(True, None))
        self.app.post_message(StatusUpdate("Thinking"))
        self._assistant_stream_saw_text = False
        run_messages = self._messages_for_prompt(text)
        result = self._runner_run(text, messages=run_messages)
        result_messages = result.get("messages")
        if isinstance(result_messages, list) and result_messages:
            self._session_messages = result_messages
        content = result.get("response", {}).get("content", [])
        response_text = "\n".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
        if response_text and not self._assistant_stream_saw_text:
            self.app.post_message(LogAppend(response_text, kind="ai"))
        self.app.post_message(SpinnerState(False, "Idle"))
        self.app.post_message(StatusUpdate("Idle"))

    def _run_plan_prompt_worker(self, text: str) -> None:
        self.app.post_message(LogAppend(f"> {text}", kind="user"))
        self._set_plan_stage("planning")
        self.app.post_message(SpinnerState(True, None))
        self.app.post_message(StatusUpdate("Planning"))
        self._assistant_stream_saw_text = False
        self._suppress_assistant_stream_text = True
        try:
            result = self._runner_plan(text)
            self._ui_call(self.app.apply_plan_result, result, True)
            self.app.post_message(SpinnerState(False, None))
            self.app.post_message(StatusUpdate("Plan ready" if result.ready_to_execute else "Plan awaiting clarification"))
        except Exception as exc:  # noqa: BLE001
            self._set_plan_stage("idle")
            self.app.post_message(SpinnerState(False, "Planning failed"))
            self.app.post_message(StatusUpdate("Planning failed"))
            self.app.post_message(LogAppend(str(exc), kind="meta"))
        finally:
            self._suppress_assistant_stream_text = False

    def _submit_plan_answer_worker(self, answer: PlanAnswer) -> None:
        self._ui_call(self.app.record_plan_answer, answer)
        self._replan_worker(auto=True)

    def _replan_worker(self, auto: bool = False) -> None:
        instruction = self._ui_call(self.app.get_plan_instruction)
        if not instruction:
            self.app.post_message(LogAppend("No active planning instruction. Use /plan first.", kind="meta"))
            return
        self._set_plan_stage("planning")
        self.app.post_message(SpinnerState(True, None))
        self.app.post_message(StatusUpdate("Planning"))
        answers = self._ui_call(self.app.get_plan_answers)
        self._assistant_stream_saw_text = False
        self._suppress_assistant_stream_text = True
        try:
            result = self._runner_plan(instruction, answers=answers)
            self._ui_call(self.app.apply_plan_result, result, False)
            self.app.post_message(SpinnerState(False, None))
            label = "Replanned" if not auto else "Plan updated"
            self.app.post_message(StatusUpdate(label if result.ready_to_execute else "Plan awaiting clarification"))
        except Exception as exc:  # noqa: BLE001
            self._set_plan_stage("idle")
            self.app.post_message(SpinnerState(False, "Planning failed"))
            self.app.post_message(StatusUpdate("Planning failed"))
            self.app.post_message(LogAppend(str(exc), kind="meta"))
        finally:
            self._suppress_assistant_stream_text = False

    def _run_execute_plan_worker(self) -> None:
        plan = self._ui_call(self.app.get_last_ready_plan)
        if plan is None or not plan.ready_to_execute:
            self.app.post_message(LogAppend("Cannot execute: no ready plan. Resolve clarifications or run /replan.", kind="meta"))
            return
        self._set_plan_stage("executing")
        self.app.post_message(LogAppend("> /execute", kind="user"))
        self.app.post_message(SpinnerState(True, None))
        self.app.post_message(StatusUpdate("Executing plan"))
        self._assistant_stream_saw_text = False
        try:
            result = self._runner_run(plan.instruction, approved_plan=plan)
            content = result.get("response", {}).get("content", [])
            response_text = "\n".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
            if response_text and not self._assistant_stream_saw_text:
                self.app.post_message(LogAppend(response_text, kind="ai"))
            self._set_plan_stage("idle")
            self.app.post_message(SpinnerState(False, "Idle"))
            self.app.post_message(StatusUpdate("Idle"))
        except Exception:
            self._set_plan_stage("ready")
            self.app.post_message(SpinnerState(False, "Execution failed"))
            self.app.post_message(StatusUpdate("Execution failed"))
            raise

    def _target_for(self, tool_name: str, payload: dict[str, Any]) -> str:
        permissions = getattr(self.runner, "permissions", None)
        if permissions and hasattr(permissions, "target_for"):
            return str(permissions.target_for(tool_name, payload))
        return "<unknown>"

    def _approval_scope_for(self, tool_name: str, payload: dict[str, Any]) -> str:
        if tool_name in {"Read", "Write", "Patch", "Edit"}:
            return f"{tool_name}:any"
        if tool_name != "Bash":
            return f"{tool_name}:exact:{self._target_for(tool_name, payload)}"

        command = str(payload.get("command", ""))
        normalized = " ".join(command.split())
        try:
            tokens = [t.lower() for t in shlex.split(command, posix=True)]
        except ValueError:
            return f"Bash:exact:{normalized}"
        if not tokens:
            return "Bash:exact:"

        safe_prefixes = {
            ("pwd",),
            ("ls",),
            ("dir",),
            ("cat",),
            ("type",),
            ("rg",),
            ("grep",),
            ("find",),
            ("head",),
            ("tail",),
            ("wc",),
        }
        test_prefixes = {
            ("pytest",),
            ("python", "-m", "pytest"),
            ("uv", "run", "pytest"),
            ("poetry", "run", "pytest"),
            ("npm", "test"),
            ("pnpm", "test"),
        }
        git_readonly_prefixes = {
            ("git", "status"),
            ("git", "diff"),
            ("git", "log"),
            ("git", "show"),
            ("git", "branch"),
        }

        lowered = tuple(tokens)
        if any(lowered[: len(prefix)] == prefix for prefix in safe_prefixes):
            return "Bash:safe"
        if any(lowered[: len(prefix)] == prefix for prefix in test_prefixes):
            return "Bash:test"
        if any(lowered[: len(prefix)] == prefix for prefix in git_readonly_prefixes):
            return "Bash:git_readonly"
        return f"Bash:exact:{normalized}"

    def request_approval(self, tool_name: str, payload: dict[str, Any]) -> bool:
        scope = self._approval_scope_for(tool_name, payload)
        if scope in self._approval_scopes:
            return True

        target = self._target_for(tool_name, payload)
        request_id = str(uuid.uuid4())
        waiter = ApprovalWaiter(event=threading.Event())
        self._approval_waiters[request_id] = waiter
        self.app.post_message(ApprovalRequest(f"Allow {tool_name} on {target}?", ["yes", "always", "no"], request_id))
        self.app.post_message(StatusUpdate("Waiting for approval"))
        waiter.event.wait()
        choice = waiter.choice or "no"
        self._approval_waiters.pop(request_id, None)
        if choice == "always":
            self._approval_scopes.add(scope)
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
        _ = RuntimeEvent.from_runner_event(event)
        etype = event.get("type")
        if etype == "planning_started":
            self.app.post_message(StatusUpdate("Planning"))
            return
        if etype == "plan_approved":
            self.app.post_message(StatusUpdate("Executing"))
            return
        if etype == "validation_started":
            self.app.post_message(StatusUpdate("Validation"))
            return
        if etype == "repair_attempt_started":
            self.app.post_message(StatusUpdate("Repairing"))
            return
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
            return
        if etype == "stream_text":
            self._assistant_stream_saw_text = True
            if self._suppress_assistant_stream_text:
                return
            self.app.post_message(LogAppend(str(event.get("text", "")), kind="stream"))
            return
