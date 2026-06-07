from __future__ import annotations

import json
import os
from pathlib import Path

from villani_code.debug_mode import build_debug_config
from villani_code.execution import ExecutionBudget
from villani_code.execution_context import (
    MAX_AGENT_TOOL_RESULT_CHARS,
    MAX_COMPACT_RETRY_MEMORY_CHARS,
    NO_PROGRESS_MESSAGE,
    PRIVATE_WARNING,
    TaskExecutionContext,
)
from villani_code.state import Runner
from villani_code.tools import execute_tool


class SequenceClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []
        self._last_response = self.responses[-1] if self.responses else {}

    def create_message(self, payload, stream=False):
        self.payloads.append(payload)
        if self.responses:
            self._last_response = self.responses.pop(0)
        return self._last_response


def _tool_response(command: str, *, timeout_sec: int = 10, tool_id: str = "tool-1") -> dict:
    return {
        "id": tool_id,
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": tool_id,
                "name": "Bash",
                "input": {"command": command, "cwd": ".", "timeout_sec": timeout_sec},
            }
        ],
    }


def _done_response() -> dict:
    return {
        "id": "done",
        "role": "assistant",
        "content": [{"type": "text", "text": "finished"}],
    }


def _run_dir(debug_root: Path) -> Path:
    directories = [path for path in debug_root.iterdir() if path.is_dir()]
    assert len(directories) == 1
    return directories[0]


def _command_rows(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "commands.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_compact_model_command_result_and_full_output_in_artifact(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    command = "printf '%07000d' 0; printf '%05000d' 0 >&2"
    client = SequenceClient([_tool_response(command), _done_response()])
    runner = Runner(
        client=client,
        repo=tmp_path,
        model="test-model",
        stream=False,
        debug_config=build_debug_config("trace", debug_root),
    )

    result = runner.run("exercise command output")
    observation_text = result["transcript"]["tool_results"][0]["content"]
    observation = json.loads(observation_text)

    assert len(observation_text) <= MAX_AGENT_TOOL_RESULT_CHARS
    assert len(observation["stdout"]) < 7000
    assert len(observation["stderr"]) < 5000
    assert "execution_context" not in observation

    debug_record = _command_rows(_run_dir(debug_root))[0]["full_debug_record"]
    assert len(debug_record["stdout"]) == 7000
    assert len(debug_record["stderr"]) == 5000
    assert debug_record["execution_context"]["before"]["environment_hash"]


def test_full_fingerprint_is_artifact_only(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    client = SequenceClient([_tool_response("printf ok"), _done_response()])
    runner = Runner(
        client=client,
        repo=tmp_path,
        model="test-model",
        stream=False,
        debug_config=build_debug_config("trace", debug_root),
    )

    result = runner.run("run one command")
    observation = json.loads(result["transcript"]["tool_results"][0]["content"])
    debug_record = _command_rows(_run_dir(debug_root))[0]["full_debug_record"]

    assert "execution_context" not in observation
    assert "environment_hash" not in observation
    assert debug_record["execution_context"]["resolved_executables"]
    assert debug_record["execution_context"]["before"]["environment_names"]


def test_private_paths_are_not_recursively_snapshotted(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    private = tmp_path / "runner-private"
    workspace.mkdir()
    private.mkdir()
    for index in range(200):
        (private / f"private-{index}").write_text("private", encoding="utf-8")
    private_reads: list[Path] = []
    original_read_bytes = Path.read_bytes

    def tracked_read_bytes(path: Path) -> bytes:
        if private in path.parents:
            private_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    context = TaskExecutionContext(workspace, private_paths=[private])
    proc, record = context.run(f"printf changed > {private / 'state'}", workspace, 10)
    context.record_validation(record, kind="command")

    assert proc.returncode == 0
    assert private_reads == []
    assert context.boundaries.classify(private / "state") == "private-runtime"
    assert PRIVATE_WARNING in record.warnings
    assert record.external_or_private_state_may_have_changed is True


def test_debug_root_is_excluded_from_workspace_snapshot(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    debug_root = workspace / "debug-artifacts"
    workspace.mkdir()
    debug_root.mkdir()
    for index in range(200):
        (debug_root / f"artifact-{index}").write_text("debug", encoding="utf-8")
    debug_reads: list[Path] = []
    original_read_bytes = Path.read_bytes

    def tracked_read_bytes(path: Path) -> bytes:
        if debug_root in path.parents:
            debug_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    context = TaskExecutionContext(workspace, snapshot_excluded_paths=[debug_root])
    context.run("printf ok", workspace, 10)

    assert debug_reads == []


def test_timeout_is_structured_and_attempt_continues(tmp_path: Path) -> None:
    client = SequenceClient(
        [
            _tool_response("sleep 2", timeout_sec=1, tool_id="timeout"),
            _tool_response("printf recovered", tool_id="recovery"),
            _done_response(),
        ]
    )
    runner = Runner(client=client, repo=tmp_path, model="test-model", stream=False)

    result = runner.run(
        "recover after timeout",
        execution_budget=ExecutionBudget(
            max_turns=5,
            max_tool_calls=5,
            max_seconds=10,
            max_no_edit_turns=5,
            max_reconsecutive_recon_turns=5,
        ),
    )
    timeout_observation = json.loads(result["transcript"]["tool_results"][0]["content"])

    assert timeout_observation["exit_code"] == 124
    assert timeout_observation["timed_out"] is True
    assert timeout_observation["message"] == "Command timed out before completion."
    assert len(result["execution"]["attempt_state"]["commands"]) == 2
    assert result["execution"]["attempt_state"]["commands"][1]["exit_code"] == 0


def test_absolute_read_outside_workspace_is_rejected_exactly(tmp_path: Path) -> None:
    requested = Path(tmp_path.anchor) / "outside-workspace-file"
    result = execute_tool("Read", {"file_path": str(requested)}, tmp_path)

    assert result["is_error"] is True
    assert result["content"] == (
        "Read is workspace-only for this path. Use a shell command such as cat, sed, or head "
        "if you need to inspect system files."
    )
    assert not (tmp_path / str(requested).lstrip("/")).exists()


def test_repeated_commands_warn_then_force_no_progress(tmp_path: Path) -> None:
    context = TaskExecutionContext(tmp_path)
    observations: list[dict] = []
    results: list[dict] = []
    for _index in range(4):
        result = execute_tool(
            "Bash",
            {"command": ":", "cwd": ".", "timeout_sec": 5},
            tmp_path,
            unsafe=True,
            execution_context=context,
        )
        results.append(result)
        observations.append(json.loads(result["content"]))

    assert observations[2]["next_action"] == NO_PROGRESS_MESSAGE
    assert results[2]["force_finalization"] is False
    assert results[3]["force_finalization"] is True


def test_retry_memory_is_compact_but_full_memory_remains_in_artifact(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    private = tmp_path / "private"
    private.mkdir()
    client = SequenceClient([_done_response()])
    runner = Runner(
        client=client,
        repo=tmp_path,
        model="test-model",
        stream=False,
        private_runtime_paths=[private],
        debug_config=build_debug_config("trace", debug_root),
    )
    runner._ensure_mission("first attempt")
    proc, record = runner._task_execution_context.run(f"printf changed > {private / 'state'}", tmp_path, 5)
    assert proc.returncode == 0
    runner._task_execution_context.record_validation(record, kind="smoke")
    memory = runner.record_final_validation(
        succeeded=False,
        summary="final validation failed " + ("detail " * 1000),
        believed_succeeded="weak check passed " + ("claim " * 1000),
    )
    assert memory is not None

    runner.run("retry")
    retry_text = next(
        block["text"]
        for message in client.payloads[0]["messages"]
        for block in message.get("content", [])
        if isinstance(block, dict) and "Previous attempt failure summary" in block.get("text", "")
    )
    artifact = json.loads((_run_dir(debug_root) / "attempt_state.json").read_text(encoding="utf-8"))

    assert len(retry_text) <= MAX_COMPACT_RETRY_MEMORY_CHARS
    assert "files_and_side_effects" not in retry_text
    assert artifact["failure_memory"]["files_and_side_effects"]
    assert len(artifact["failure_memory"]["believed_succeeded"]) > len(retry_text)


def test_isolation_preserved_and_resolved_executable_recorded(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    private = tmp_path / "private"
    workspace.mkdir()
    private.mkdir()
    executable = private / "private-command"
    executable.write_text("#!/bin/sh\nprintf private", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(private), os.environ.get("PATH", "")]))
    monkeypatch.setenv("VIRTUAL_ENV", str(private))
    context = TaskExecutionContext(workspace, private_paths=[private])

    proc, record = context.run("printf '%s' \"$PATH\"", workspace, 5)

    assert str(private) not in proc.stdout.split(os.pathsep)
    assert str(private) not in record.before.path.split(os.pathsep)
    assert "VIRTUAL_ENV" not in record.before.environment_names
    assert record.resolved_executables


def test_runner_forces_stop_after_second_no_progress_event(tmp_path: Path) -> None:
    client = SequenceClient(
        [
            _tool_response(":", tool_id="repeat-1"),
            _tool_response(":", tool_id="repeat-2"),
            _tool_response(":", tool_id="repeat-3"),
            _tool_response(":", tool_id="repeat-4"),
        ]
    )
    runner = Runner(client=client, repo=tmp_path, model="test-model", stream=False)

    result = runner.run(
        "avoid repeated work",
        execution_budget=ExecutionBudget(
            max_turns=8,
            max_tool_calls=8,
            max_seconds=15,
            max_no_edit_turns=8,
            max_reconsecutive_recon_turns=8,
        ),
    )

    assert result["execution"]["terminated_reason"] == "no_progress"
    assert result["execution"]["attempt_state"]["no_progress_events"] == 2
    assert len(client.payloads) == 4


def test_workspace_snapshot_cap_is_recorded(tmp_path: Path, monkeypatch) -> None:
    import villani_code.execution_context as execution_context

    for index in range(6):
        (tmp_path / f"file-{index}").write_text(str(index), encoding="utf-8")
    monkeypatch.setattr(execution_context, "MAX_SNAPSHOT_FILES", 3)
    context = TaskExecutionContext(tmp_path)

    _proc, record = context.run("printf ok", tmp_path, 5)

    assert record.snapshot_truncated is True
    assert record.to_dict()["snapshot_truncated"] is True


def test_runner_policy_does_not_remap_absolute_read_path(tmp_path: Path) -> None:
    runner = Runner(client=SequenceClient([]), repo=tmp_path, model="test-model", stream=False)
    requested = Path(tmp_path.anchor) / "outside-workspace-file"

    result = runner._execute_tool_with_policy(
        "Read", {"file_path": str(requested)}, "read-outside", 0
    )

    assert result["content"] == (
        "Read is workspace-only for this path. Use a shell command such as cat, sed, or head "
        "if you need to inspect system files."
    )


def test_repeated_reads_participate_in_no_progress_guard(tmp_path: Path) -> None:
    target = tmp_path / "note"
    target.write_text("content", encoding="utf-8")
    runner = Runner(client=SequenceClient([]), repo=tmp_path, model="test-model", stream=False)
    results = [
        runner._execute_tool_with_policy("Read", {"file_path": "note"}, f"read-{index}", 0)
        for index in range(4)
    ]

    assert NO_PROGRESS_MESSAGE in results[2]["content"]
    assert results[3]["force_finalization"] is True
