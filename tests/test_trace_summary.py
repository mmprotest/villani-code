from __future__ import annotations

import json
from pathlib import Path

import pytest

from villani_code.trace_summary import (
    EventLogger,
    aggregate_summary_from_events,
    build_tool_call_records_from_events,
    validate_summary,
    write_summary_from_events,
    write_tool_calls_from_events,
)


def _logger(tmp_path: Path) -> tuple[Path, EventLogger]:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = EventLogger("run-1", run_dir / "events.jsonl")
    logger.emit("run_started", {"objective": "test"})
    return run_dir, logger


def _finish_run(logger: EventLogger) -> None:
    logger.emit("run_completed", {"termination_reason": "completed"})


def _emit_tool(
    logger: EventLogger,
    *,
    tool_id: str,
    name: str,
    ok: bool = True,
    turn_index: int | None = None,
    args: dict | None = None,
) -> None:
    logger.emit("tool_call_started", {"tool_name": name, "tool_call_id": tool_id, "args": args or {}}, turn_index=turn_index)
    if ok:
        logger.emit(
            "tool_call_completed",
            {"tool_name": name, "tool_call_id": tool_id, "status": "completed", "result_summary": {"summary": "ok"}},
            turn_index=turn_index,
        )
    else:
        logger.emit(
            "tool_call_failed",
            {"tool_name": name, "tool_call_id": tool_id, "status": "failed", "summary": "boom"},
            turn_index=turn_index,
        )


def test_turn_indexing_and_turn_counts(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    for i in range(1, 4):
        logger.emit("turn_started", {"message_count": 2}, turn_index=i)
        logger.emit("turn_finished", {"stop_reason": "tool_use"}, turn_index=i)
    _finish_run(logger)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["turn_count"] == 3


def test_bash_tool_finalization_from_tool_result(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Bash", "tool_call_id": "bash-1", "args": {"command": "pwd", "cwd": "."}}, turn_index=1)
    logger.emit("command_started", {"command": "pwd", "cwd": ".", "tool_call_id": "bash-1"}, turn_index=1)
    logger.emit(
        "command_finished",
        {"command": "pwd", "cwd": ".", "tool_call_id": "bash-1", "exit_code": 0, "stdout": "/tmp\n", "stderr": "", "truncated": False},
        turn_index=1,
    )
    logger.emit(
        "tool_call_completed",
        {"tool_name": "Bash", "tool_call_id": "bash-1", "summary": "ok", "result": {"content": "ok"}},
        turn_index=1,
    )
    logger.emit("tool_finished", {"name": "Bash", "tool_call_id": "bash-1"}, turn_index=1)
    _finish_run(logger)

    rows = [json.loads(line) for line in write_tool_calls_from_events(run_dir).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["result_summary"]["kind"] == "command_result"
    assert rows[0]["result_summary"]["exit_code"] == 0
    assert "stdout_preview" in rows[0]["result_summary"]
    assert "stderr_preview" in rows[0]["result_summary"]


def test_write_tool_row_enrichment(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Write", "tool_call_id": "w1", "args": {"file_path": "a.py"}}, turn_index=1)
    logger.emit("file_write", {"tool_call_id": "w1", "file_path": "a.py", "size_bytes": 12, "lines_written": 1}, turn_index=1)
    logger.emit("tool_call_completed", {"tool_name": "Write", "tool_call_id": "w1", "summary": "ok"}, turn_index=1)
    logger.emit("tool_finished", {"name": "Write", "tool_call_id": "w1"}, turn_index=1)
    _finish_run(logger)

    rows = [json.loads(line) for line in write_tool_calls_from_events(run_dir).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["result_summary"]["kind"] == "file_write_result"
    assert rows[0]["result_summary"]["path"] == "a.py"
    assert rows[0]["result_summary"]["bytes_written"] == 12


def test_patch_tool_row_enrichment(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Patch", "tool_call_id": "p1", "args": {"file_path": "a.py"}}, turn_index=1)
    logger.emit(
        "file_patch_applied",
        {"tool_call_id": "p1", "file_path": "a.py", "bytes_delta": 10, "lines_added": 2, "lines_removed": 1},
        turn_index=1,
    )
    logger.emit("tool_call_completed", {"tool_name": "Patch", "tool_call_id": "p1", "summary": "ok"}, turn_index=1)
    logger.emit("tool_finished", {"name": "Patch", "tool_call_id": "p1"}, turn_index=1)
    _finish_run(logger)

    rows = [json.loads(line) for line in write_tool_calls_from_events(run_dir).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["result_summary"]["kind"] == "file_patch_result"
    assert rows[0]["result_summary"]["bytes_delta"] == 10


def test_unterminated_tool_is_materialized_and_warned(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Write", "tool_call_id": "t1", "args": {"file_path": "a.py"}}, turn_index=1)
    _finish_run(logger)

    rows = [json.loads(line) for line in write_tool_calls_from_events(run_dir).read_text(encoding="utf-8").splitlines()]
    summary = aggregate_summary_from_events(run_dir)
    assert len(rows) == 1
    assert rows[0]["status"] == "partial"
    assert rows[0]["result_summary"]["kind"] == "unterminated_tool_call"
    assert any("Unterminated tool calls" in w for w in summary.get("aggregation_warnings", []))


def test_command_to_tool_mapping_enforced(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("command_finished", {"tool_call_id": "missing", "command": "pwd", "cwd": ".", "exit_code": 0}, turn_index=1)
    _finish_run(logger)

    with pytest.raises(ValueError, match="matching Bash tool-call records"):
        aggregate_summary_from_events(run_dir)


def test_no_duplicate_run_started_or_terminal(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    _emit_tool(logger, tool_id="t1", name="Read", args={"file_path": "x.py"})
    _finish_run(logger)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["status"] == "completed"


def test_duplicate_run_started_fails(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("run_started", {"objective": "dup"})
    _finish_run(logger)

    with pytest.raises(ValueError, match="exactly one canonical run_started"):
        aggregate_summary_from_events(run_dir)


def test_tool_calls_jsonl_is_final_only(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    _emit_tool(logger, tool_id="t1", name="Read", args={"file_path": "x.py"})
    _finish_run(logger)

    rows = [json.loads(line) for line in write_tool_calls_from_events(run_dir).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert "event_type" not in rows[0]
    assert rows[0]["schema_version"] == "v1"


def test_path_normalization_dedupes_mixed_forms(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (run_dir / "session_meta.json").write_text(json.dumps({"repo": str(repo_dir)}), encoding="utf-8")

    logger.emit("file_read", {"file_path": "src/a.py"})
    logger.emit("file_read", {"file_path": str((repo_dir / "src/a.py").resolve())})
    _finish_run(logger)

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
    _finish_run(logger)

    tool_path = write_tool_calls_from_events(run_dir)
    summary_path = write_summary_from_events(run_dir)

    rows = tool_path.read_text(encoding="utf-8").splitlines()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert len(rows) == summary["total_tool_calls"]


def test_artifact_manifest_marks_optional_absent_artifacts_honestly(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    _finish_run(logger)
    summary = aggregate_summary_from_events(run_dir)
    validations = summary["artifacts"]["validations.jsonl"]
    assert validations["optional"] is True
    assert validations["exists"] is False
    assert validations["state"] == "absent_optional"


def test_summary_cleanliness_regression_keeps_core_counts_stable(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    repo_dir = tmp_path / "repo-clean"
    repo_dir.mkdir()
    (run_dir / "session_meta.json").write_text(json.dumps({"repo": str(repo_dir)}), encoding="utf-8")

    logger.emit("turn_started", {"message_count": 1}, turn_index=1)
    logger.emit("tool_call_started", {"tool_name": "Write", "tool_call_id": "w1", "args": {"file_path": "src/a.py"}}, turn_index=1)
    logger.emit("file_write", {"tool_call_id": "w1", "file_path": "src/a.py", "size_bytes": 5}, turn_index=1)
    logger.emit("file_write", {"tool_call_id": "w1", "file_path": str((repo_dir / "src/a.py").resolve()), "size_bytes": 5}, turn_index=1)
    logger.emit("tool_call_completed", {"tool_name": "Write", "tool_call_id": "w1", "summary": "ok"}, turn_index=1)
    logger.emit("tool_call_started", {"tool_name": "Bash", "tool_call_id": "b1", "args": {"command": "pwd", "cwd": "."}}, turn_index=1)
    logger.emit("command_started", {"tool_call_id": "b1", "command": "pwd", "cwd": "."}, turn_index=1)
    logger.emit("command_finished", {"tool_call_id": "b1", "command": "pwd", "cwd": ".", "exit_code": 0, "stdout": "", "stderr": "", "truncated": False}, turn_index=1)
    logger.emit("tool_call_completed", {"tool_name": "Bash", "tool_call_id": "b1", "exit_code": 0, "summary": "ok"}, turn_index=1)
    logger.emit("turn_finished", {"stop_reason": "end_turn"}, turn_index=1)
    _finish_run(logger)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["turn_count"] == 1
    assert summary["total_tool_calls"] == 2
    assert summary["commands_executed"] == 1
    assert summary["total_file_writes"] == 2
    assert summary["unique_files_written"] == 1
    assert "validation_errors" not in summary
    assert summary["artifacts"]["validations.jsonl"]["state"] == "absent_optional"


def test_duplicate_tool_lifecycle_fails_validation(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Read", "tool_call_id": "t1", "args": {"file_path": "a.py"}}, turn_index=1)
    logger.emit("tool_call_started", {"tool_name": "Read", "tool_call_id": "t1", "args": {"file_path": "a.py"}}, turn_index=1)
    logger.emit("tool_call_completed", {"tool_name": "Read", "tool_call_id": "t1"}, turn_index=1)
    _finish_run(logger)

    with pytest.raises(ValueError, match="duplicate tool_call_started"):
        write_tool_calls_from_events(run_dir)


def test_file_event_mapping_enforced(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("file_write", {"tool_call_id": "missing", "file_path": "a.py", "size_bytes": 1}, turn_index=1)
    _finish_run(logger)

    with pytest.raises(ValueError, match="canonical file events contain tool_call_id values without matching tool-call records"):
        aggregate_summary_from_events(run_dir)


def test_model_request_counts_align_and_no_duplicate_ids(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("turn_started", {"message_count": 1}, turn_index=1)
    logger.emit("model_request_started", {"request_id": "mr-1", "model": "demo", "message_count": 1}, turn_index=1)
    logger.emit(
        "model_request_completed",
        {"request_id": "mr-1", "tokens_input": 3, "tokens_output": 2, "tokens_total": 5},
        turn_index=1,
    )
    logger.emit("turn_finished", {"stop_reason": "end_turn"}, turn_index=1)
    _finish_run(logger)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["model_requests"] == 1
    assert summary["model_failures"] == 0
    assert summary["tokens_input"] == 3
    assert summary["tokens_output"] == 2


def test_duplicate_model_request_start_for_same_request_id_fails(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("model_request_started", {"request_id": "mr-1"}, turn_index=1)
    logger.emit("model_request_started", {"request_id": "mr-1"}, turn_index=1)
    logger.emit("model_request_completed", {"request_id": "mr-1"}, turn_index=1)
    _finish_run(logger)

    with pytest.raises(ValueError, match="duplicate model_request_started"):
        aggregate_summary_from_events(run_dir)


def test_patch_failure_event_counts_and_patch_tool_mapping(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Patch", "tool_call_id": "p-fail", "args": {"file_path": "a.py"}}, turn_index=1)
    logger.emit(
        "file_patch_failed",
        {
            "tool_call_id": "p-fail",
            "file_path": "a.py",
            "failure_reason": "hunk mismatch",
            "hunks_attempted": 1,
            "hunks_failed": 1,
        },
        turn_index=1,
    )
    logger.emit("tool_call_failed", {"tool_name": "Patch", "tool_call_id": "p-fail", "summary": "Patch failed"}, turn_index=1)
    _finish_run(logger)

    rows = build_tool_call_records_from_events(run_dir)[0]
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["result_summary"]["kind"] == "file_patch_result"
    assert rows[0]["result_summary"]["ok"] is False
    summary = aggregate_summary_from_events(run_dir)
    assert summary["tool_failures_by_name"]["Patch"] == 1
    assert summary["total_file_patch_failures"] == 1
    assert summary["total_file_patches_applied"] == 0


def test_patch_success_counts_still_work(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Patch", "tool_call_id": "p-ok", "args": {"file_path": "a.py"}}, turn_index=1)
    logger.emit("file_patch_applied", {"tool_call_id": "p-ok", "file_path": "a.py"}, turn_index=1)
    logger.emit("tool_call_completed", {"tool_name": "Patch", "tool_call_id": "p-ok", "summary": "ok"}, turn_index=1)
    _finish_run(logger)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["total_file_patches_applied"] == 1
    assert summary["total_file_patch_failures"] == 0


def test_failed_patch_tool_without_patch_failure_event_fails(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Patch", "tool_call_id": "p-missing", "args": {"file_path": "a.py"}}, turn_index=1)
    logger.emit("tool_call_failed", {"tool_name": "Patch", "tool_call_id": "p-missing", "summary": "Patch failed"}, turn_index=1)
    _finish_run(logger)

    with pytest.raises(ValueError, match="missing canonical file_patch_failed events"):
        aggregate_summary_from_events(run_dir)

def test_reliability_metrics_in_summary(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit(
        "task_outcome_contract_created",
        {"contract": {"required_observables": ["a", "b"], "behavioral_checks": ["x"]}},
        turn_index=1,
    )
    logger.emit("contract_satisfaction_checked", {"satisfied": False}, turn_index=1)
    logger.emit("completion_gate_blocked", {"reason": "missing evidence"}, turn_index=1)
    logger.emit("recovery_packet_injected", {"reason": "stall"}, turn_index=2)
    logger.emit(
        "progress_assessed",
        {"repeated_file_patch": True, "repeated_failed_command": True, "repeated_verification": False},
        turn_index=2,
    )
    logger.emit("feedback_interpretation_created", {"summary": "do x"}, turn_index=2)
    logger.emit("contract_satisfaction_checked", {"satisfied": True}, turn_index=3)
    _finish_run(logger)

    summary = aggregate_summary_from_events(run_dir)
    assert summary["contract_required_observables_count"] == 2
    assert summary["contract_behavioral_checks_count"] == 1
    assert summary["contract_satisfaction_final"] is True
    assert summary["completion_gate_blocks"] == 1
    assert summary["recovery_packets_injected"] == 1
    assert summary["repeated_file_patch_stalls"] == 1
    assert summary["repeated_failed_command_stalls"] == 1
    assert summary["repeated_verification_stalls"] == 0
    assert summary["feedback_interpretations_created"] == 1


def test_reliability_metrics_safe_defaults_for_old_or_missing_events(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    _finish_run(logger)
    summary = aggregate_summary_from_events(run_dir)

    assert summary["contract_required_observables_count"] is None
    assert summary["contract_behavioral_checks_count"] is None
    assert summary["contract_satisfaction_final"] is None
    assert summary["completion_gate_blocks"] == 0
    assert summary["recovery_packets_injected"] == 0
    assert summary["repeated_file_patch_stalls"] == 0
    assert summary["repeated_failed_command_stalls"] == 0
    assert summary["repeated_verification_stalls"] == 0
    assert summary["feedback_interpretations_created"] == 0
