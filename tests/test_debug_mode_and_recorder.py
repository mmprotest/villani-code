from __future__ import annotations

import json
from pathlib import Path

from villani_code.debug_artifacts import create_debug_run_artifacts, resolve_debug_root
from villani_code.debug_mode import DebugMode, build_debug_config, parse_debug_mode
from villani_code.debug_recorder import DebugRecorder


def test_debug_mode_parsing() -> None:
    assert parse_debug_mode(None) == DebugMode.OFF
    assert parse_debug_mode("off") == DebugMode.OFF
    assert parse_debug_mode("normal") == DebugMode.NORMAL
    assert parse_debug_mode("trace") == DebugMode.TRACE


def test_debug_root_creation(tmp_path: Path) -> None:
    artifacts = create_debug_run_artifacts("run-123", debug_root=tmp_path)
    assert artifacts.run_dir.exists()
    assert resolve_debug_root(tmp_path) == tmp_path


def test_recorder_writes_events_and_summary(tmp_path: Path) -> None:
    config = build_debug_config("trace", tmp_path)
    recorder = DebugRecorder(config, run_id="r1", objective="fix", repo=tmp_path, mode="execution", model="demo")
    recorder.record_event("run_started", "Run started")
    recorder.record_turn_start(1, {"message_count": 2})
    recorder.record_model_request({"model": "demo", "messages": [{"role": "user", "content": []}]})
    recorder.record_model_response({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    recorder.record_tool_call("Read", {"file_path": "a.py"}, "tool-1")
    recorder.record_tool_result("Read", False, "ok")
    recorder.record_command_start("pytest", ".")
    recorder.record_command_finish("pytest", ".", 0, "passed", "", False)
    recorder.record_file_read("a.py", 30, True)
    recorder.record_file_write("a.py", 10, True)
    recorder.record_patch_applied("a.py", True)
    recorder.record_approval_requested("Write", {"file_path": "a.py"})
    recorder.record_approval_resolved("Write", True, {"file_path": "a.py"})
    recorder.record_validation_start("post_execution", {"changed_files": ["a.py"]})
    recorder.record_validation_finish("post_execution", 0, "passed")
    recorder.record_mission_state_snapshot({"status": "active"}, "start")
    summary_path = recorder.write_final_summary(status="completed", termination_reason="completed", total_turns=1, mission_id="m1")

    events_path = tmp_path / "r1" / "events.jsonl"
    assert events_path.exists()
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["total_turns"] == 1
    assert "a.py" in summary["changed_files"]


def test_trace_model_io_capture_vs_normal(tmp_path: Path) -> None:
    trace = DebugRecorder(build_debug_config("trace", tmp_path), "t", "obj", tmp_path, "execution", "m")
    trace.record_model_request({"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]})
    normal = DebugRecorder(build_debug_config("normal", tmp_path), "n", "obj", tmp_path, "execution", "m")
    normal.record_model_request({"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]})

    trace_row = json.loads((tmp_path / "t" / "model_requests.jsonl").read_text(encoding="utf-8").splitlines()[0])
    normal_row = json.loads((tmp_path / "n" / "model_requests.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "messages" in trace_row["payload"]
    assert "message_count" in normal_row["payload"]
