from pathlib import Path

from villani_code.state import Runner


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.first_tool_result_seen = False

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
        assert payload["messages"][-1]["role"] == "user"
        assert payload["messages"][-1]["content"][0]["type"] == "tool_result"
        assert payload["messages"][-1]["content"][0]["tool_use_id"] == "tool-123"
        self.first_tool_result_seen = True
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
    assert client.first_tool_result_seen


class FakeMultiToolClient:
    def __init__(self):
        self.calls = 0

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "A", "name": "Ls", "input": {"path": "."}},
                    {"type": "tool_use", "id": "B", "name": "Ls", "input": {"path": "."}},
                ],
            }

        user_msg = payload["messages"][-1]
        assert user_msg["role"] == "user"
        assert [block["type"] for block in user_msg["content"]] == ["tool_result", "tool_result"]
        assert [block["tool_use_id"] for block in user_msg["content"]] == ["A", "B"]
        return {"id": "2", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_runner_batches_tool_results_and_blocks_next_model_call_until_appended(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "villani_code.state.execute_tool",
        lambda tool_name, tool_input, repo, unsafe=False: {"content": f"ok-{tool_name}", "is_error": False},
    )
    client = FakeMultiToolClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    result = runner.run("run tools")

    assert "error" not in result
    assert client.calls == 2
