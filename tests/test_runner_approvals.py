from pathlib import Path

from villani_code.permissions import PermissionConfig, PermissionEngine
from villani_code.state import Runner


class AskToolClient:
    def __init__(self):
        self.calls = 0

    def create_message(self, _payload, stream):
        assert stream is False
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Ls", "input": {"path": "."}}],
            }
        return {"id": "2", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def _ask_runner(tmp_path: Path, approved: bool) -> Runner:
    runner = Runner(client=AskToolClient(), repo=tmp_path, model="m", stream=False)
    runner.permissions = PermissionEngine(
        PermissionConfig.from_strings(deny=[], ask=["Ls(*)"], allow=[]),
        repo=tmp_path,
    )
    runner.approval_callback = lambda _tool, _payload: approved
    return runner


def test_runner_denied_ask_blocks_tool_execution(tmp_path: Path) -> None:
    runner = _ask_runner(tmp_path, approved=False)

    result = runner.run("list files")

    tool_result = next(m for m in result["messages"] if m["role"] == "user" and m["content"][0].get("type") == "tool_result")
    assert tool_result["content"][0]["is_error"] is True
    assert "User denied tool execution" in tool_result["content"][0]["content"]


def test_runner_approved_ask_runs_tool(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    runner = _ask_runner(tmp_path, approved=True)

    result = runner.run("list files")

    tool_result = next(m for m in result["messages"] if m["role"] == "user" and m["content"][0].get("type") == "tool_result")
    assert tool_result["content"][0]["is_error"] is False
    assert "a.txt" in tool_result["content"][0]["content"]


def _protected_runner(tmp_path: Path, approved: bool = False) -> tuple[Runner, list[dict]]:
    events: list[dict] = []
    runner = Runner(client=AskToolClient(), repo=tmp_path, model="m", stream=False)
    runner.event_callback = events.append
    runner.approval_callback = lambda _tool, _payload: approved
    return runner, events


def test_write_still_emits_approval_required(tmp_path: Path) -> None:
    runner, events = _protected_runner(tmp_path)

    result = runner._execute_tool_with_policy("Write", {"file_path": "a.txt", "content": "x"}, "toolu_1", 0)

    assert result["is_error"] is True
    assert any(event.get("type") == "approval_required" and event.get("name") == "Write" for event in events)


def test_patch_still_emits_approval_required(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old\n", encoding="utf-8")
    runner, events = _protected_runner(tmp_path)
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"

    result = runner._execute_tool_with_policy("Patch", {"file_path": "a.txt", "diff": diff}, "toolu_1", 0)

    assert result["is_error"] is True
    assert any(event.get("type") == "approval_required" and event.get("name") == "Patch" for event in events)


def test_unsafe_bash_still_emits_approval_required(tmp_path: Path) -> None:
    runner, events = _protected_runner(tmp_path)

    result = runner._execute_tool_with_policy("Bash", {"command": "pip install package-name"}, "toolu_1", 0)

    assert result["is_error"] is True
    assert any(event.get("type") == "approval_required" and event.get("name") == "Bash" for event in events)
