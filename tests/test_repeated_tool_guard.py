from __future__ import annotations

from pathlib import Path

from villani_code.state import Runner
from villani_code import state_tooling


class DummyClient:
    def create_message(self, _payload, stream):
        _ = stream
        return {"content": [{"type": "text", "text": "ok"}]}


def _runner(tmp_path: Path) -> Runner:
    return Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)


def test_repeated_same_tool_same_args_same_result_blocked_after_threshold(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path)
    calls = {"count": 0}

    def fake_execute(**_kwargs):
        calls["count"] += 1
        return {"content": "", "is_error": False}

    monkeypatch.setattr(state_tooling, "execute_tool_with_lifecycle", fake_execute)
    for idx in range(3):
        result = runner._execute_tool_with_policy("Read", {"file_path": "a.py"}, f"tool-{idx}", idx)
        assert result["is_error"] is False
    blocked = runner._execute_tool_with_policy("Read", {"file_path": "a.py"}, "tool-3", 3)
    assert calls["count"] == 3
    assert "Repeated no-progress tool call blocked" in str(blocked["content"])


def test_same_tool_different_args_not_blocked(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path)
    calls = {"count": 0}

    def fake_execute(**_kwargs):
        calls["count"] += 1
        return {"content": "", "is_error": False}

    monkeypatch.setattr(state_tooling, "execute_tool_with_lifecycle", fake_execute)
    runner._execute_tool_with_policy("Read", {"file_path": "a.py"}, "tool-0", 0)
    runner._execute_tool_with_policy("Read", {"file_path": "a.py"}, "tool-1", 1)
    runner._execute_tool_with_policy("Read", {"file_path": "a.py"}, "tool-2", 2)
    result = runner._execute_tool_with_policy("Read", {"file_path": "b.py"}, "tool-3", 3)
    assert calls["count"] == 4
    assert "Repeated no-progress tool call blocked" not in str(result["content"])


def test_same_args_different_result_not_blocked(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path)
    calls = {"count": 0}

    def fake_execute(**_kwargs):
        calls["count"] += 1
        return {"content": "" if calls["count"] % 2 else "non-empty", "is_error": False}

    monkeypatch.setattr(state_tooling, "execute_tool_with_lifecycle", fake_execute)
    for idx in range(4):
        runner._execute_tool_with_policy("Read", {"file_path": "a.py"}, f"tool-{idx}", idx)
    assert calls["count"] == 4


def test_argument_whitespace_normalization(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path)

    def fake_execute(**_kwargs):
        return {"content": "", "is_error": False}

    monkeypatch.setattr(state_tooling, "execute_tool_with_lifecycle", fake_execute)
    for idx in range(3):
        runner._execute_tool_with_policy("Bash", {"command": "echo   hello"}, f"tool-a-{idx}", idx)
    blocked = runner._execute_tool_with_policy("Bash", {"command": "echo hello"}, "tool-b", 3)
    assert "Repeated no-progress tool call blocked" in str(blocked["content"])


def test_block_returns_synthetic_message_without_executing(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path)
    calls = {"count": 0}

    def fake_execute(**_kwargs):
        calls["count"] += 1
        return {"content": "", "is_error": False}

    monkeypatch.setattr(state_tooling, "execute_tool_with_lifecycle", fake_execute)
    for idx in range(3):
        runner._execute_tool_with_policy("Read", {"file_path": "same.py"}, f"tool-{idx}", idx)
    blocked = runner._execute_tool_with_policy("Read", {"file_path": "same.py"}, "tool-4", 4)
    assert calls["count"] == 3
    assert "Choose a different action" in str(blocked["content"])
