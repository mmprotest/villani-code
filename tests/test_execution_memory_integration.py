from pathlib import Path

from villani_code.state import Runner


class _RepeatClient:
    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[dict] = []

    def create_message(self, payload, stream):
        self.calls += 1
        self.payloads.append(payload)
        if self.calls <= 2:
            return {
                "id": str(self.calls),
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"tool-{self.calls}",
                        "name": "Bash",
                        "input": {"command": "python -m pytest src/app.py"},
                    }
                ],
            }
        return {"id": "3", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_runner_injects_execution_memory_and_escalates_repeat_warning(tmp_path: Path) -> None:
    client = _RepeatClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    runner._git_changed_files = lambda: []

    def _failing_tool(*_args, **_kwargs):
        return {"is_error": True, "content": "No module named pytest"}

    runner._execute_tool_with_policy = _failing_tool
    runner.run("fix test run")

    assert len(client.payloads) >= 3
    final_system = client.payloads[2]["system"]
    execution_memory_block = final_system[-1]["text"]
    assert "<execution-memory>" in execution_memory_block
    assert "Repeat-risk signal:" in execution_memory_block
    assert "warning" in execution_memory_block.lower()


def test_runner_repeat_concern_drops_after_relevant_file_change(tmp_path: Path) -> None:
    client = _RepeatClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    state = {"edited": False}

    def _changed_files():
        return ["src/app.py"] if state["edited"] else []

    runner._git_changed_files = _changed_files

    def _failing_tool(tool_name, tool_input, *_args, **_kwargs):
        if not state["edited"]:
            state["edited"] = True
            return {"is_error": True, "content": "AssertionError in src/app.py"}
        return {"is_error": True, "content": "TypeError in src/app.py line 42"}

    runner._execute_tool_with_policy = _failing_tool
    runner.run("fix test run")

    assert len(client.payloads) >= 3
    execution_memory_block = client.payloads[2]["system"][-1]["text"]
    assert "Repeat-risk signal: No strong repeat concern." in execution_memory_block
