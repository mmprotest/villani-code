from __future__ import annotations

import json
from pathlib import Path

import pytest

from villani_code.trace_summary import EventLogger, aggregate_summary_from_events, write_summary_from_events


def _logger(tmp_path: Path) -> tuple[Path, EventLogger]:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, EventLogger("run-1", run_dir / "events.jsonl")


def test_basic_tool_counting(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("run_started", {})
    logger.emit("tool_call_started", {"tool_name": "Bash", "tool_call_id": "t1", "args": {"command": "echo hi"}})
    logger.emit("tool_call_completed", {"tool_name": "Bash", "tool_call_id": "t1", "status": "completed", "exit_code": 0})
    logger.emit("tool_call_started", {"tool_name": "Write", "tool_call_id": "t2", "args": {"file_path": "a.py"}})
    logger.emit("tool_call_completed", {"tool_name": "Write", "tool_call_id": "t2", "status": "completed"})
    logger.emit("file_write", {"file_path": "a.py", "size_bytes": 10, "line_count": 1, "tool_call_id": "t2"})
    logger.emit("run_completed", {})

    summary = aggregate_summary_from_events(run_dir)

    assert summary["total_tool_calls"] == 2
    assert summary["tool_calls_by_name"] == {"Bash": 1, "Write": 1}
    assert summary["total_file_writes"] == 1


def test_failed_tool_counting(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Bash", "tool_call_id": "t1", "args": {"command": "pytest"}})
    logger.emit(
        "tool_call_failed",
        {"tool_name": "Bash", "tool_call_id": "t1", "status": "failed", "error_type": "exit_non_zero", "exit_code": 1},
    )

    summary = aggregate_summary_from_events(run_dir)

    assert summary["total_tool_calls"] == 1
    assert summary["tool_failures_by_name"]["Bash"] == 1
    assert summary["commands_executed"] == 1
    assert summary["commands_failed"] == 1


def test_unterminated_started_tool_warning(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    logger.emit("tool_call_started", {"tool_name": "Write", "tool_call_id": "t1", "args": {"file_path": "a.py"}})

    summary = aggregate_summary_from_events(run_dir)

    assert summary["total_tool_calls"] == 1
    warnings = summary.get("aggregation_warnings", [])
    assert any("Unterminated tool calls" in w for w in warnings)


def test_real_trace_style_replay_counts(tmp_path: Path) -> None:
    run_dir, logger = _logger(tmp_path)
    counter = 0
    for name, total in (("Bash", 20), ("Write", 14), ("Ls", 2)):
        for _ in range(total):
            counter += 1
            tool_id = f"t{counter}"
            logger.emit("tool_call_started", {"tool_name": name, "tool_call_id": tool_id, "args": {}})
            logger.emit("tool_call_completed", {"tool_name": name, "tool_call_id": tool_id, "status": "completed", "exit_code": 0})

    summary = aggregate_summary_from_events(run_dir)

    assert summary["total_tool_calls"] > 0
    assert summary["tool_calls_by_name"]["Bash"] == 20
    assert summary["tool_calls_by_name"]["Write"] == 14
    assert summary["tool_calls_by_name"]["Ls"] == 2


def test_artifact_validation_required_missing_fails(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(FileNotFoundError):
        write_summary_from_events(run_dir)


def test_legacy_tool_signal_avoids_false_zero(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"event_id": 1, "run_id": "run-1", "ts": "2026-04-01T00:00:00Z", "event_type": "tool_use", "payload": {"name": "Bash"}},
    ]
    (run_dir / "events.jsonl").write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")

    summary = aggregate_summary_from_events(run_dir)

    assert summary["total_tool_calls"] is None
    assert any("No canonical tool_call_started events" in w for w in summary.get("aggregation_warnings", []))
