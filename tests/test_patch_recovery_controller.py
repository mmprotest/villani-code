from __future__ import annotations

from pathlib import Path

from villani_code.state import Runner


class _PatchFailThenDoneClient:
    def __init__(self) -> None:
        self.calls = 0
        self.second_payload: dict | None = None

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Patch",
                        "input": {
                            "file_path": "src/a.py",
                            "unified_diff": "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-missing\n+patched\n",
                        },
                    }
                ],
            }
        self.second_payload = payload
        return {"id": "2", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


class _PatchSuccessThenDoneClient:
    def __init__(self) -> None:
        self.calls = 0
        self.second_payload: dict | None = None

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Patch",
                        "input": {
                            "file_path": "src/a.py",
                            "unified_diff": "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n",
                        },
                    }
                ],
            }
        self.second_payload = payload
        return {"id": "2", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


class _PatchFailThenBashThenDoneClient:
    def __init__(self, command: str) -> None:
        self.calls = 0
        self.command = command
        self.third_payload: dict | None = None

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Patch",
                        "input": {
                            "file_path": "src/a.py",
                            "unified_diff": "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-missing\n+patched\n",
                        },
                    }
                ],
            }
        if self.calls == 2:
            return {
                "id": "2",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-2",
                        "name": "Bash",
                        "input": {"command": self.command},
                    }
                ],
            }
        self.third_payload = payload
        return {"id": "3", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def _find_injected_recovery_text(payload: dict) -> str:
    for message in payload.get("messages", []):
        if message.get("role") != "user":
            continue
        for block in message.get("content", []):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = str(block.get("text", ""))
            if "Patch recovery: the previous patch failed" in text:
                return text
    return ""


def test_patch_failure_records_structured_state_and_refreshes_context(tmp_path: Path) -> None:
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\nsecond\n", encoding="utf-8")

    client = _PatchFailThenDoneClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, plan_mode="off")
    runner.run("fix whatever task shape")

    assert runner._mission_state is not None
    patch_state = runner._mission_state.last_patch_failure
    assert patch_state["target_file"] == "src/a.py"
    assert patch_state["next_required_action"] == "retry_patch_against_refreshed_context"
    assert "src/a.py (total_lines=" in patch_state["refreshed_context"]
    assert "old" in patch_state["refreshed_context"]
    assert patch_state["attempts_for_file"] == 1
    assert runner._mission_state.recent_tool_failures


def test_patch_failure_injects_single_turn_recovery_instruction(tmp_path: Path) -> None:
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")

    client = _PatchFailThenDoneClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, plan_mode="off")
    runner.run("some benchmark fastapi language-specific task")

    assert client.second_payload is not None
    injected = _find_injected_recovery_text(client.second_payload)
    assert "target_file: src/a.py" in injected
    assert "patch_error:" in injected
    assert "refreshed_context:" in injected


def test_patch_recovery_blocks_immediate_bash_rewrite_of_same_target(tmp_path: Path) -> None:
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    events: list[dict] = []
    command = "python -c \"from pathlib import Path; Path('src/a.py').write_text('rewritten\\n')\""
    client = _PatchFailThenBashThenDoneClient(command)
    runner = Runner(
        client=client,
        repo=tmp_path,
        model="m",
        stream=False,
        plan_mode="off",
        event_callback=events.append,
    )
    runner.run("generic task")

    assert client.third_payload is not None
    tool_result_messages = [
        message
        for message in client.third_payload["messages"]
        if message.get("role") == "user"
        and message.get("content")
        and isinstance(message["content"][0], dict)
        and message["content"][0].get("type") == "tool_result"
    ]
    assert any("Patch recovery active" in str(block.get("content", "")) for msg in tool_result_messages for block in msg["content"])
    assert any(event.get("type") == "patch_recovery_bash_blocked" for event in events)


def test_patch_recovery_allows_read_only_and_test_bash_commands(tmp_path: Path) -> None:
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")

    client_read = _PatchFailThenBashThenDoneClient("cat src/a.py")
    runner_read = Runner(client=client_read, repo=tmp_path, model="m", stream=False, plan_mode="off")
    runner_read.run("generic task")
    assert client_read.third_payload is not None
    assert "Patch recovery active" not in str(client_read.third_payload)

    client_test = _PatchFailThenBashThenDoneClient("pytest -q")
    runner_test = Runner(client=client_test, repo=tmp_path, model="m", stream=False, plan_mode="off")
    runner_test.run("generic task")
    assert client_test.third_payload is not None
    assert "Patch recovery active" not in str(client_test.third_payload)


def test_patch_recovery_does_not_trigger_when_patch_succeeds(tmp_path: Path) -> None:
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")

    client = _PatchSuccessThenDoneClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, plan_mode="off")
    runner.run("generic task")

    assert client.second_payload is not None
    assert _find_injected_recovery_text(client.second_payload) == ""
    assert runner._mission_state is not None
    assert runner._mission_state.last_patch_failure == {}
