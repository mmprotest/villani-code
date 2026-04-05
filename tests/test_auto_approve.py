from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from villani_code import cli
from villani_code.state import Runner


class DummyClient:
    def create_message(self, _payload, stream):
        _ = stream
        return {"content": [{"type": "text", "text": "ok"}]}


def test_approval_flow_unchanged_without_auto_approve(tmp_path: Path) -> None:
    callback_calls = {"count": 0}

    def approval_callback(_tool_name: str, _payload: dict[str, object]) -> bool:
        callback_calls["count"] += 1
        return False

    runner = Runner(
        client=DummyClient(),
        repo=tmp_path,
        model="m",
        stream=False,
        approval_callback=approval_callback,
    )
    result = runner._execute_tool_with_policy(
        "Write",
        {"file_path": "a.txt", "content": "x", "mkdirs": True},
        "toolu_1",
        0,
    )
    assert callback_calls["count"] == 1
    assert result["is_error"] is True
    assert result["content"] == "User denied tool execution"


def test_auto_approve_immediately_approves_without_prompt(tmp_path: Path) -> None:
    callback_calls = {"count": 0}

    def approval_callback(_tool_name: str, _payload: dict[str, object]) -> bool:
        callback_calls["count"] += 1
        return False

    events: list[dict[str, object]] = []
    runner = Runner(
        client=DummyClient(),
        repo=tmp_path,
        model="m",
        stream=False,
        approval_callback=approval_callback,
        auto_approve=True,
    )
    runner.event_callback = events.append
    result = runner._execute_tool_with_policy(
        "Write",
        {"file_path": "a.txt", "content": "x", "mkdirs": True},
        "toolu_1",
        0,
    )
    assert callback_calls["count"] == 0
    assert result["is_error"] is False
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x"
    assert any(event.get("type") == "approval_auto_resolved" for event in events)
    assert not any(event.get("type") == "approval_resolved" for event in events)


def test_auto_approve_tracing_marks_auto_source(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    runner = Runner(
        client=DummyClient(),
        repo=tmp_path,
        model="m",
        stream=False,
        auto_approve=True,
    )
    runner.event_callback = events.append
    runner._execute_tool_with_policy(
        "Write",
        {"file_path": "a.txt", "content": "x", "mkdirs": True},
        "toolu_1",
        0,
    )
    auto_event = next(event for event in events if event.get("type") == "approval_auto_resolved")
    assert auto_event["approval_mode"] == "auto"
    assert auto_event["decision_source"] == "auto_approve_flag"
    assert auto_event["approved"] is True


def test_tui_skips_approval_ui_when_auto_approve_enabled() -> None:
    pytest.importorskip("textual")
    from villani_code.tui.controller import RunnerController

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

    class AutoApproveRunner:
        permissions = None
        print_stream = False
        approval_callback = None
        event_callback = None
        auto_approve = True

        def run(self, instruction: str, messages=None, execution_budget=None, approved_plan=None):
            _ = (instruction, messages, execution_budget, approved_plan)
            return {"response": {"content": []}}

        def plan(self, instruction: str, answers=None):
            _ = (instruction, answers)
            raise RuntimeError("unused")

        def run_villani_mode(self):
            return {"response": {"content": []}}

    app = DummyApp()
    controller = RunnerController(AutoApproveRunner(), app)
    assert controller.request_approval("Write", {"file_path": "a.txt"}) is True
    assert not any(message.__class__.__name__ == "ApprovalRequest" for message in app.messages)


def test_cli_run_sets_auto_approve_runtime_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class DummyRunner:
        def run(self, _instruction: str):
            return {"response": {"content": [{"type": "text", "text": "ok"}]}}

    def fake_build_runner(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return DummyRunner()

    monkeypatch.setattr(cli, "_build_runner", fake_build_runner)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "run",
            "do thing",
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
            "--auto-approve",
        ],
    )
    assert result.exit_code == 0
    args = captured.get("args")
    assert isinstance(args, tuple)
    assert args[12] is True
    assert "Auto-approval: ON" in result.stdout
