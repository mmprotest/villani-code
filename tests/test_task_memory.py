import json
from pathlib import Path

from villani_code.state import Runner
from villani_code.task_memory import JSONL_FILES, TaskMemory


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_memory_initialization_creates_isolated_artifacts(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    memory = TaskMemory(tmp_path, "run-1")

    memory.initialize()

    assert memory.run_dir == tmp_path / ".villani" / "memory" / "run-1"
    assert all((memory.run_dir / name).exists() for name in JSONL_FILES)
    assert "pyproject.toml" in (memory.run_dir / "repo_summary.md").read_text(encoding="utf-8")
    assert (memory.run_dir / "current_state.md").exists()


def test_jsonl_append_command_truncation_and_validation_signal(tmp_path: Path) -> None:
    memory = TaskMemory(tmp_path, "run-1")
    memory.initialize()

    memory.record_command(command="pytest -q", cwd=".", exit_code=1, stdout="x" * 5000, stderr="failure clue")

    command = _rows(memory.run_dir / "command_history.jsonl")[0]
    assert len(command["stdout_tail"]) == 4000
    assert command["summary"] == "command failed"
    signal = _rows(memory.run_dir / "test_signals.jsonl")[0]
    assert signal["passed"] is False
    assert "failure clue" in signal["summary"]


def test_current_state_search_and_recent_retrieval_tools(tmp_path: Path) -> None:
    memory = TaskMemory(tmp_path, "run-1")
    memory.initialize()
    memory.record_inspection("src/app.py")
    memory.record_change("src/app.py", "edit")
    memory.record_command(command="pytest", cwd=".", exit_code=1, stdout="FAILED widget", stderr="")
    memory.record_command(command="python -m compileall src", cwd=".", exit_code=0, stdout="ok", stderr="")
    memory.regenerate_current_state()

    assert "src/app.py" in memory.search("app.py")
    assert "src/app.py" in memory.execute_tool("memory_inspected_files", {"limit": 3})["content"]
    assert "src/app.py" in memory.execute_tool("memory_changed_files", {"limit": 3})["content"]
    assert "compileall" in memory.execute_tool("memory_recent_commands", {"limit": 1})["content"]
    failures = memory.execute_tool("memory_recent_failures", {"limit": 3})["content"]
    assert "pytest" in failures and "FAILED widget" in failures
    state = (memory.run_dir / "current_state.md").read_text(encoding="utf-8")
    assert "# Current State" in state
    assert "## Current failures" in state


def test_semantic_memory_and_automatic_change_types(tmp_path: Path) -> None:
    memory = TaskMemory(tmp_path, "run-1")
    memory.initialize()

    memory.observe_tool_result(
        "Write",
        {"file_path": "new.txt", "content": "x"},
        {"content": "ok", "is_error": False},
        target_existed_before=False,
    )
    hypothesis = memory.execute_tool(
        "memory_record_hypothesis",
        {"hypothesis": "The parser rejects empty input", "status": "active", "evidence": "test output"},
    )
    dead_end = memory.execute_tool(
        "memory_record_dead_end",
        {"attempt": "retry broad test", "why_failed": "same failure", "avoid": "use targeted test"},
    )

    assert _rows(memory.run_dir / "code_changes.jsonl")[0]["change_type"] == "create"
    assert hypothesis["is_error"] is False
    assert dead_end["is_error"] is False
    assert _rows(memory.run_dir / "hypotheses.jsonl")[0]["status"] == "active"
    assert _rows(memory.run_dir / "dead_ends.jsonl")[0]["avoid"] == "use targeted test"


def test_memory_failures_are_non_fatal(tmp_path: Path, monkeypatch) -> None:
    memory = TaskMemory(tmp_path, "run-1")
    memory.initialize()

    def fail_write(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "open", fail_write)
    memory.record_inspection("src/app.py")
    result = memory.execute_tool("memory_search", {"query": "anything"})

    assert result["is_error"] is False


class CapturingClient:
    def __init__(self, tool_name: str = "Bash", tool_input: dict | None = None):
        self.calls = 0
        self.payloads: list[dict] = []
        self.tool_name = tool_name
        self.tool_input = {"command": "pytest -q"} if tool_input is None else tool_input

    def create_message(self, payload, stream):
        self.calls += 1
        self.payloads.append(payload)
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool-1", "name": self.tool_name, "input": self.tool_input}],
            }
        return {"id": "2", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_runner_memory_is_disabled_by_default(tmp_path: Path) -> None:
    client = CapturingClient("Ls", {"path": "."})
    result = Runner(client=client, repo=tmp_path, model="m", stream=False).run("inspect")

    assert not (tmp_path / ".villani" / "memory").exists()
    assert result["telemetry"]["memory_enabled"] is False
    assert all(not spec["name"].startswith("memory_") for spec in client.payloads[0]["tools"])


def test_runner_memory_writes_stay_out_of_transcript_and_artifacts_are_not_injected(tmp_path: Path) -> None:
    client = CapturingClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, memory_enabled=True, plan_mode="off")

    result = runner.run("run validation")

    memory_dir = Path(result["telemetry"]["memory_run_dir"])
    assert _rows(memory_dir / "command_history.jsonl")
    assert _rows(memory_dir / "test_signals.jsonl")
    first_payload_text = json.dumps(client.payloads[0])
    assert "# Current State" not in first_payload_text
    assert "Project type:" not in first_payload_text
    transcript_text = json.dumps(result["transcript"])
    assert "command_history.jsonl" not in transcript_text
    assert not any(
        message.get("role") == "user"
        and any("# Current State" in str(block.get("text", "")) for block in message.get("content", []) if isinstance(block, dict))
        for message in result["messages"]
    )


def test_memory_tool_result_is_the_only_retrieved_memory_added_to_messages(tmp_path: Path) -> None:
    client = CapturingClient("memory_get_repo_summary", {})
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, memory_enabled=True, plan_mode="off")

    result = runner.run("inspect memory")

    tool_results = [
        block
        for message in result["messages"]
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    assert "Project type:" in tool_results[0]["content"]
    assert result["telemetry"]["memory_tool_calls"] == 1
