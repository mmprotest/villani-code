from __future__ import annotations

import json
from pathlib import Path

import pytest

from villani_code.trace_summary import (
    EventLogger,
    aggregate_summary_from_events,
    validate_summary,
    write_summary_from_events,
    write_tool_calls_from_events,
)


def _logger(tmp_path: Path) -> tuple[Path, EventLogger]:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, EventLogger("run-1", run_dir / "events.jsonl")


def _emit_tool(logger: EventLogger, *, tool_id: str, name: str, ok: bool = True, turn_index: int | None = None, args: dict | None = None) -> None:
    logger.emit("tool_call_started", {"tool_name": name, "tool_call_id": tool_id, "args": args or {}}, turn_index=turn_index)
    if ok:
        logger.emit("tool_call_completed", {"tool_name": name, "tool_call_id": tool_id, "status": "completed", "result_summary": {"summary": "ok"}}, turn_index=turn_index)
    else:
        logger.emit("tool_call_failed", {"tool_name": name, "tool_call_id": tool_id, "status": "failed", "summary": "boom"}, turn_index=turn_index)


def test_turn_indexing_and_turn_counts(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    turns_path = run_dir / "turns.jsonl"
    turns_path.write_text(
        "\n".join(
            json.dumps({"ts": f"2026-04-01T00:00:0{i}Z", "turn_index": i, "payload": {"message_count": 2}})
            for i in range(1, 4)
        )
        + "\n",
        encoding="utf-8",
    )
    for i in range(1, 4):
        logger.emit("turn_started", {"message_count": 2}, turn_index=i)
        logger.emit("turn_finished", {"stop_reason": "tool_use"}, turn_index=i)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["turn_count"] == 3


def test_basic_tool_lifecycle_and_tool_calls_file(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    _emit_tool(logger, tool_id="t1", name="Bash", args={"command": "echo hi"})
    _emit_tool(logger, tool_id="t2", name="Write", args={"file_path": "a.py"})
    _emit_tool(logger, tool_id="t3", name="Read", args={"file_path": "a.py"})

    tool_calls_path = write_tool_calls_from_events(run_dir)
    rows = [json.loads(line) for line in tool_calls_path.read_text(encoding="utf-8").splitlines()]
    summary = aggregate_summary_from_events(run_dir)

    assert len(rows) == 3
    assert summary["total_tool_calls"] == 3
    assert summary["tool_calls_by_name"] == {"Bash": 1, "Write": 1, "Read": 1}


def test_command_alignment_uses_canonical_shell_tools(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    _emit_tool(logger, tool_id="bash-1", name="Bash", args={"command": "pwd", "cwd": "."})
    logger.emit("command_started", {"command": "pwd", "cwd": ".", "tool_call_id": "bash-1"})
    logger.emit("command_finished", {"command": "pwd", "cwd": ".", "tool_call_id": "bash-1", "exit_code": 0})
    (run_dir / "commands.jsonl").write_text(json.dumps({"command": "pwd", "tool_call_id": "bash-1"}) + "\n", encoding="utf-8")

    summary = aggregate_summary_from_events(run_dir)
    assert summary["commands_executed"] == 1
    assert summary["commands_failed"] == 0


def test_failed_tool_call_is_accounted(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    _emit_tool(logger, tool_id="t1", name="Bash", ok=False, args={"command": "exit 1"})

    rows = [json.loads(line) for line in write_tool_calls_from_events(run_dir).read_text(encoding="utf-8").splitlines()]
    summary = aggregate_summary_from_events(run_dir)

    assert rows[0]["status"] == "failed"
    assert summary["tool_failures_by_name"]["Bash"] == 1
    assert summary["commands_failed"] == 1


def test_unterminated_tool_is_flagged(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Write", "tool_call_id": "t1", "args": {"file_path": "a.py"}})

    summary = aggregate_summary_from_events(run_dir)
    assert summary["total_tool_calls"] == 1
    assert any("Unterminated tool calls" in w for w in summary.get("aggregation_warnings", []))


def test_token_aggregation_non_null(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("model_request_started", {"model": "demo"}, turn_index=1)
    logger.emit("model_request_completed", {"tokens_input": 100, "tokens_output": 40}, turn_index=1)
    logger.emit("model_request_completed", {"tokens_input": 50, "tokens_output": 10}, turn_index=2)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["tokens_input"] == 150
    assert summary["tokens_output"] == 50


def test_token_aggregation_null_when_absent(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("model_request_started", {"model": "demo"}, turn_index=1)
    logger.emit("model_request_completed", {"stop_reason": "end_turn"}, turn_index=1)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["tokens_input"] is None
    assert summary["tokens_output"] is None


def test_path_normalization_dedupes_mixed_forms(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (run_dir / "session_meta.json").write_text(json.dumps({"repo": str(repo_dir)}), encoding="utf-8")

    logger.emit("file_read", {"file_path": "src/a.py"})
    logger.emit("file_read", {"file_path": str((repo_dir / "src/a.py").resolve())})

    summary = aggregate_summary_from_events(run_dir)
    assert summary["total_file_reads"] == 2
    assert summary["unique_files_read"] == 1


def test_artifact_validation_fails_on_missing_claimed_artifact() -> None:
    with pytest.raises(ValueError):
        validate_summary(
            {
                "total_tool_calls": 0,
                "tool_calls_by_name": {},
                "commands_executed": 0,
                "files_touched": 0,
                "unique_files_read": 0,
                "unique_files_written": 0,
                "artifacts": {
                    "tool_calls.jsonl": {
                        "path": "/tmp/does-not-exist.jsonl",
                        "exists": True,
                        "optional": False,
                    }
                },
            }
        )


def test_rebuild_commands_generate_equivalent_outputs(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    _emit_tool(logger, tool_id="t1", name="Read", args={"file_path": "x.py"})

    tool_path = write_tool_calls_from_events(run_dir)
    summary_path = write_summary_from_events(run_dir)

    rows = tool_path.read_text(encoding="utf-8").splitlines()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert len(rows) == summary["total_tool_calls"]


def test_problematic_real_trace_like_fixture(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    tool_idx = 0
    for turn in range(1, 8):
        logger.emit("turn_started", {"message_count": 3}, turn_index=turn)
        logger.emit("model_request_started", {"model": "demo"}, turn_index=turn)
        logger.emit("model_request_completed", {"tokens_input": 20, "tokens_output": 10}, turn_index=turn)
        for name in ["Bash", "Write", "Patch", "Read", "Ls", "Grep"]:
            tool_idx += 1
            _emit_tool(logger, tool_id=f"t{tool_idx}", name=name, turn_index=turn, args={"file_path": f"f{turn}.py", "command": "echo hi"})
        logger.emit("file_write", {"file_path": f"f{turn}.py"}, turn_index=turn)
        logger.emit("turn_finished", {"stop_reason": "tool_use"}, turn_index=turn)

    write_tool_calls_from_events(run_dir)
    summary = aggregate_summary_from_events(run_dir)

    assert summary["turn_count"] == 7
    assert summary["total_tool_calls"] == 42
    assert summary["commands_executed"] == 14
    assert summary["tokens_input"] == 140
    assert summary["tokens_output"] == 70
    assert (run_dir / "tool_calls.jsonl").exists()
