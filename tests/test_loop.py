from pathlib import Path

from villani_code.state import Runner


class FakeClient:
    def __init__(self):
        self.calls = 0

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-123", "name": "Ls", "input": {"path": "."}}
                ],
            }
        return {
            "id": "2",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        }


def test_loop_appends_tool_result_with_matching_id(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    client = FakeClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    result = runner.run("list files")

    tool_result_messages = [
        m for m in result["messages"] if m["role"] == "user" and m["content"][0].get("type") == "tool_result"
    ]
    assert tool_result_messages
    assert tool_result_messages[0]["content"][0]["tool_use_id"] == "tool-123"
    assert client.calls == 2


class FakeClientTwoTools:
    def __init__(self):
        self.calls = 0
        self.second_payload = None

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Ls", "input": {"path": "."}},
                    {"type": "tool_use", "id": "tool-2", "name": "Ls", "input": {"path": "."}},
                ],
            }
        self.second_payload = payload
        return {
            "id": "2",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        }


def test_loop_batches_multiple_tool_results_in_single_user_message(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    client = FakeClientTwoTools()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    runner.run("list files")

    assert client.second_payload is not None
    user_messages = [m for m in client.second_payload["messages"] if m["role"] == "user" and m["content"] and m["content"][0].get("type") == "tool_result"]
    assert len(user_messages) == 1
    assert [b["tool_use_id"] for b in user_messages[0]["content"]] == ["tool-1", "tool-2"]
