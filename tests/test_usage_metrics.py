from pathlib import Path

from villani_code.state import Runner


class UsageClient:
    def create_message(self, payload, stream):
        return {
            "id": "1",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 6},
            "stop_reason": "end_turn",
        }


class ToolResultClient:
    def __init__(self) -> None:
        self.calls = 0

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": "a.txt", "max_bytes": 100000}}],
            }
        return {"id": "2", "role": "assistant", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}


def test_usage_update_event_emitted_on_model_stop(tmp_path: Path):
    events = []

    def on_event(event):
        events.append(event)

    runner = Runner(client=UsageClient(), repo=tmp_path, model="m", stream=False, stream_event_handler=on_event)
    runner.run("hello")
    usage_events = [e for e in events if e.get("type") == "usage_update"]
    assert usage_events
    assert usage_events[-1]["input_tokens"] == 10
    assert usage_events[-1]["cache_read_input_tokens"] == 6


def test_runner_compacts_large_tool_result_in_messages(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x" * 100000, encoding="utf-8")
    runner = Runner(client=ToolResultClient(), repo=tmp_path, model="m", stream=False)
    result = runner.run("read")
    tool_result_message = [m for m in result["messages"] if m["role"] == "user" and m["content"] and m["content"][0].get("type") == "tool_result"][0]
    content = tool_result_message["content"][0]["content"]
    assert "...[truncated]" in content
    assert len(content) < 42000
