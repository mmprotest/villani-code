from __future__ import annotations

from pathlib import Path

from villani_code.state import Runner
from villani_code.tools import execute_tool


class _SequenceClient:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = responses
        self._idx = 0

    def create_message(self, _payload, stream=False):
        response = self._responses[self._idx]
        self._idx += 1
        return response


def test_completion_is_not_blocked_after_text_only_turn(tmp_path: Path) -> None:
    client = _SequenceClient(
        [
            {"id": "1", "role": "assistant", "content": [{"type": "text", "text": "Implemented fix in src/app.py"}]},
        ]
    )
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    result = runner.run("Implement a code fix")

    final_text_blocks = [b for b in result["response"]["content"] if b.get("type") == "text"]
    assert final_text_blocks


def test_repeated_malformed_tool_calls_trigger_schema_recovery_message(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    events: list[dict] = []
    client = _SequenceClient(
        [
            {"id": "1", "role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "Write", "input": {}}]},
            {"id": "2", "role": "assistant", "content": [{"type": "tool_use", "id": "w2", "name": "Write", "input": {}}]},
            {
                "id": "3",
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "README.md"}}],
            },
            {"id": "4", "role": "assistant", "content": [{"type": "text", "text": "Done"}]},
        ]
    )
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)
    runner.event_callback = events.append

    result = runner.run("Inspect the repo")

    assert any(e.get("type") == "tool_schema_recovery_triggered" for e in events)
    message_texts: list[str] = []
    for msg in result["messages"]:
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                message_texts.append(str(block.get("text", "")))
    assert any("Recent Write calls were invalid" in text for text in message_texts)


def test_windows_incompatible_shell_form_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    result = execute_tool(
        "Bash",
        {"command": "cat <<'EOF'\nhello\nEOF", "cwd": ".", "timeout_sec": 5},
        repo=tmp_path,
        unsafe=True,
    )
    assert result["is_error"] is True
    assert "invalid in Windows shells" in str(result["content"])
