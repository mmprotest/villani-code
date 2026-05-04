from __future__ import annotations

from pathlib import Path

from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy


class _Client:
    def create_message(self, _payload, stream):
        assert stream is False
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


class _Hooks:
    def run_event(self, *_args, **_kwargs):
        return type("Hook", (), {"allow": True, "reason": ""})()


class _PermissivePermissions:
    def evaluate_with_reason(self, *_args, **_kwargs):
        from villani_code.permissions import Decision

        return type("P", (), {"decision": Decision.ALLOW, "reason": ""})()


def _runner(tmp_path: Path) -> Runner:
    runner = Runner(client=_Client(), repo=tmp_path, model="m", stream=False, plan_mode="off")
    runner.hooks = _Hooks()
    runner.permissions = _PermissivePermissions()
    return runner


def test_identical_repeat_blocked_on_fourth_attempt(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    path = tmp_path / "a.txt"
    path.write_text("hello\n", encoding="utf-8")

    for _ in range(3):
        result = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)
        assert result["is_error"] is False
        assert "hello" in str(result["content"])

    blocked = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)
    assert blocked["is_error"] is False
    assert "Loop breaker" in str(blocked["content"])


def test_same_tool_and_args_not_blocked_when_observation_changes(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    path = tmp_path / "a.txt"
    path.write_text("one\n", encoding="utf-8")

    _ = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)
    path.write_text("two\n", encoding="utf-8")
    second = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)
    assert "two" in str(second["content"])

    third = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)
    assert "two" in str(third["content"])


def test_same_tool_with_different_args_not_blocked(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")

    for _ in range(3):
        _ = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)

    other = execute_tool_with_policy(runner, "Read", {"file_path": "b.txt"}, "1", 0)
    assert "b" in str(other["content"])


def test_tracking_resets_between_task_runs(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    runner._tool_repeat_history = {"k": ["v", "v", "v"]}
    runner.run("Read a file.", messages=[{"role": "user", "content": [{"type": "text", "text": "done"}]}])
    assert runner._tool_repeat_history == {}


def test_synthetic_observation_shape_matches_normal_tool_result(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    normal = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)
    for _ in range(2):
        _ = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)
    synthetic = execute_tool_with_policy(runner, "Read", {"file_path": "a.txt"}, "1", 0)
    assert set(synthetic.keys()) == set(normal.keys())
