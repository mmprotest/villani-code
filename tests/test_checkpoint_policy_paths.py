from pathlib import Path

from villani_code.permissions import PermissionConfig, PermissionEngine
from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy


class DummyClient:
    def create_message(self, _payload, stream):
        raise AssertionError("not used")


def _runner(tmp_path: Path) -> Runner:
    events = []
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, plan_mode="off", event_callback=events.append)
    runner._events = events
    return runner


def test_checkpoint_created_for_allow_path(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.permissions = PermissionEngine(PermissionConfig.from_strings(deny=[], ask=[], allow=["Write(*)"]), repo=tmp_path)
    result = execute_tool_with_policy(runner, "Write", {"file_path": "a.txt", "content": "x"}, "1", 1)
    assert result["is_error"] is False
    assert any(e.get("type") == "checkpoint_created" for e in runner._events)


def test_checkpoint_created_for_ask_approved_path(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.permissions = PermissionEngine(PermissionConfig.from_strings(deny=[], ask=["Write(*)"], allow=[]), repo=tmp_path)
    runner.approval_callback = lambda _tool, _payload: True
    result = execute_tool_with_policy(runner, "Write", {"file_path": "a.txt", "content": "x"}, "1", 1)
    assert result["is_error"] is False
    assert any(e.get("type") == "approval_requested" for e in runner._events)
    assert any(e.get("type") == "approval_resolved" and e.get("approved") is True for e in runner._events)
    assert any(e.get("type") == "checkpoint_created" for e in runner._events)
