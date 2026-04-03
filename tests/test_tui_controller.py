from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("textual")

from villani_code.tui.controller import RunnerController
from villani_code.tui.messages import LogAppend, SpinnerState, StatusUpdate


class DummyApp:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def post_message(self, message: object) -> object:
        self.messages.append(message)
        return message

    def call_from_thread(self, callback, *args, **kwargs):
        return callback(*args, **kwargs)

    def apply_plan_result(self, _result, _reset_answers: bool) -> None:
        return None

    def record_plan_answer(self, _answer) -> None:
        return None

    def get_plan_instruction(self) -> str:
        return ""

    def get_plan_answers(self) -> list:
        return []

    def get_last_ready_plan(self):
        return None

    def set_plan_stage(self, stage: str) -> None:
        _ = stage


class ExplodingRunner:
    def __init__(self) -> None:
        self.print_stream = True
        self.approval_callback = None
        self.event_callback = None
        self.permissions = None

    def run(self, instruction: str, messages=None, execution_budget=None, approved_plan=None):
        _ = (instruction, messages, execution_budget, approved_plan)
        return {"response": {"content": []}}

    def plan(self, instruction: str, answers=None):
        _ = (instruction, answers)
        raise RuntimeError("unused")

    def run_with_plan(self, plan):
        _ = plan
        return {"response": {"content": []}}

    def run_villani_mode(self):
        raise RuntimeError("boom")


class ApprovalRunner:
    permissions = None
    print_stream = False
    approval_callback = None
    event_callback = None

    def run(self, instruction: str, messages=None, execution_budget=None, approved_plan=None):
        _ = (instruction, messages, execution_budget, approved_plan)
        return {"response": {"content": []}}

    def plan(self, instruction: str, answers=None):
        _ = (instruction, answers)
        raise RuntimeError("unused")

    def run_with_plan(self, plan):
        _ = plan
        return {"response": {"content": []}}

    def run_villani_mode(self):
        return {"response": {"content": []}}


def _request_with_choice(controller: RunnerController, app: DummyApp, tool: str, payload: dict[str, str], choice: str) -> bool:
    approved: dict[str, bool] = {}

    def worker() -> None:
        approved["value"] = controller.request_approval(tool, payload)

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 1
    while time.time() < deadline:
        req = next((m for m in app.messages if m.__class__.__name__ == "ApprovalRequest"), None)
        if req is not None:
            controller.resolve_approval(req.request_id, choice)
            break
        time.sleep(0.01)
    thread.join(timeout=1)
    return approved["value"]


def test_tui_worker_logs_exception() -> None:
    app = DummyApp()
    controller = RunnerController(ExplodingRunner(), app)

    controller._run_villani_mode_worker()

    logs = [m for m in app.messages if isinstance(m, LogAppend)]
    assert any("ERROR RuntimeError: boom" in m.text for m in logs)
    assert any("Traceback" in m.text for m in logs)
    assert any(isinstance(m, SpinnerState) and m.active is False for m in app.messages)
    assert any(isinstance(m, StatusUpdate) and m.text == "Idle" for m in app.messages)


def test_always_scopes_read_across_different_files_in_session() -> None:
    app = DummyApp()
    controller = RunnerController(ApprovalRunner(), app)

    assert _request_with_choice(controller, app, "Read", {"file_path": "a.txt"}, "always") is True
    app.messages.clear()
    assert controller.request_approval("Read", {"file_path": "b.txt"}) is True
    assert not any(m.__class__.__name__ == "ApprovalRequest" for m in app.messages)


def test_always_scopes_patch_across_different_files_in_session() -> None:
    app = DummyApp()
    controller = RunnerController(ApprovalRunner(), app)

    assert _request_with_choice(controller, app, "Patch", {"file_path": "a.txt"}, "always") is True
    app.messages.clear()
    assert controller.request_approval("Patch", {"file_path": "b.txt"}) is True
    assert not any(m.__class__.__name__ == "ApprovalRequest" for m in app.messages)


def test_always_scopes_git_readonly_bash_commands_in_session() -> None:
    app = DummyApp()
    controller = RunnerController(ApprovalRunner(), app)

    assert _request_with_choice(controller, app, "Bash", {"command": "git status"}, "always") is True
    app.messages.clear()
    assert controller.request_approval("Bash", {"command": "git   log --oneline"}) is True
    assert not any(m.__class__.__name__ == "ApprovalRequest" for m in app.messages)


def test_always_scope_does_not_auto_approve_unrelated_categories() -> None:
    app = DummyApp()
    controller = RunnerController(ApprovalRunner(), app)

    assert _request_with_choice(controller, app, "Read", {"file_path": "a.txt"}, "always") is True
    app.messages.clear()
    assert _request_with_choice(controller, app, "Write", {"file_path": "a.txt"}, "no") is False
    assert any(m.__class__.__name__ == "ApprovalRequest" for m in app.messages)


class StreamRunner:
    print_stream = False
    approval_callback = None
    event_callback = None
    permissions = None

    def run(self, instruction: str, messages=None, execution_budget=None, approved_plan=None):
        _ = (instruction, messages, execution_budget, approved_plan)
        return {"response": {"content": []}}

    def plan(self, instruction: str, answers=None):
        _ = (instruction, answers)
        raise RuntimeError("unused")

    def run_with_plan(self, plan):
        _ = plan
        return {"response": {"content": []}}

    def run_villani_mode(self):
        return {"response": {"content": []}}


def test_stream_text_is_suppressed_during_plan_mode() -> None:
    app = DummyApp()
    controller = RunnerController(StreamRunner(), app)
    controller._suppress_assistant_stream_text = True
    controller.on_runner_event({"type": "stream_text", "text": "raw planning json"})
    assert not any(isinstance(m, LogAppend) and m.kind == "stream" for m in app.messages)


def test_stream_text_still_renders_outside_plan_mode() -> None:
    app = DummyApp()
    controller = RunnerController(StreamRunner(), app)
    controller._suppress_assistant_stream_text = False
    controller.on_runner_event({"type": "stream_text", "text": "normal output"})
    assert any(isinstance(m, LogAppend) and m.kind == "stream" and "normal output" in m.text for m in app.messages)


def test_execute_uses_standard_run_path_with_approved_plan() -> None:
    class ExecuteRunner(StreamRunner):
        def __init__(self) -> None:
            self.run_calls: list[object] = []
            self.run_with_plan_calls = 0

        def run(self, instruction: str, messages=None, execution_budget=None, approved_plan=None):
            self.run_calls.append((instruction, approved_plan))
            return {"response": {"content": []}}

        def run_with_plan(self, plan):
            _ = plan
            self.run_with_plan_calls += 1
            return {"response": {"content": []}}

    class ExecuteApp(DummyApp):
        def __init__(self, plan) -> None:
            super().__init__()
            self._plan = plan

        def get_last_ready_plan(self):
            return self._plan

    from villani_code.plan_session import PlanSessionResult

    plan = PlanSessionResult(instruction="implement fix", task_summary="fix", ready_to_execute=True)
    runner = ExecuteRunner()
    controller = RunnerController(runner, ExecuteApp(plan))
    controller._run_execute_plan_worker()

    assert runner.run_with_plan_calls == 0
    assert runner.run_calls == [("implement fix", plan)]
