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


def test_final_summary_changed_files_are_normalized_and_deduped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "norm", "obj", repo, "execution", "m")
    recorder.record_turn_start(1, {"message_count": 1})
    recorder.record_file_write("src/a.py", 10, True, turn_index=1)
    recorder.record_patch_applied(str((repo / "src/a.py").resolve()), True, turn_index=1)
    recorder.record_file_write("./src/a.py", 1, True, turn_index=1)
    recorder.record_patch_applied("src/b.py", True, turn_index=1)
    recorder.record_turn_finish(1, "end_turn")
    summary_path = recorder.write_final_summary(status="completed", termination_reason="completed", total_turns=1, mission_id="m1")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["changed_files"] == ["src/a.py", "src/b.py"]


def test_trace_model_io_capture_vs_normal(tmp_path: Path) -> None:
    trace = DebugRecorder(build_debug_config("trace", tmp_path), "t", "obj", tmp_path, "execution", "m")
    trace.record_model_request({"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]})
    normal = DebugRecorder(build_debug_config("normal", tmp_path), "n", "obj", tmp_path, "execution", "m")
    normal.record_model_request({"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]})

    trace_row = json.loads((tmp_path / "t" / "model_requests.jsonl").read_text(encoding="utf-8").splitlines()[0])
    normal_row = json.loads((tmp_path / "n" / "model_requests.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "messages" in trace_row["payload"]
    assert "message_count" in normal_row["payload"]


def test_single_model_request_lifecycle_has_one_start_and_one_completion(tmp_path: Path) -> None:
    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "mr", "obj", tmp_path, "execution", "m")
    recorder.record_turn_start(1, {"message_count": 1})
    recorder.record_model_request({"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]})
    recorder.record_model_response(
        {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19},
        }
    )
    recorder.record_turn_finish(1, "end_turn")
    recorder.write_final_summary(status="completed", termination_reason="completed", total_turns=1, mission_id="m")

    events = [json.loads(line) for line in (tmp_path / "mr" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    starts = [e for e in events if e["event_type"] == "model_request_started"]
    terminals = [e for e in events if e["event_type"] in {"model_request_completed", "model_request_failed"}]
    assert len(starts) == 1
    assert len(terminals) == 1
    assert starts[0]["turn_index"] == 1
    assert terminals[0]["turn_index"] == 1
    assert terminals[0]["payload"]["tokens_input"] == 12
    assert terminals[0]["payload"]["tokens_output"] == 7


def test_model_request_started_runner_event_is_not_double_recorded(tmp_path: Path) -> None:
    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "mr2", "obj", tmp_path, "execution", "m")
    recorder.record_turn_start(1, {"message_count": 1})
    recorder.record_model_request({"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]})
    recorder.on_runner_event({"type": "model_request_started", "model": "m", "turn_index": 1})
    recorder.record_model_response({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    recorder.write_final_summary(status="completed", termination_reason="completed", total_turns=1, mission_id="m")

    events = [json.loads(line) for line in (tmp_path / "mr2" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(1 for e in events if e["event_type"] == "model_request_started") == 1


def test_approval_event_records_turn_index_when_available(tmp_path: Path) -> None:
    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "approve", "obj", tmp_path, "execution", "m")
    recorder.on_runner_event({"type": "approval_resolved", "name": "Write", "approved": True, "input": {"file_path": "a.py"}, "turn_index": 4})
    events = [json.loads(line) for line in (tmp_path / "approve" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    approval = next(e for e in events if e["event_type"] == "approval_resolved")
    assert approval["turn_index"] == 4


def test_mission_state_event_records_turn_index_when_available(tmp_path: Path) -> None:
    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "mission", "obj", tmp_path, "execution", "m")
    recorder.record_mission_state_snapshot({"status": "active"}, "mission_state_update", turn_index=3)
    events = [json.loads(line) for line in (tmp_path / "mission" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    mission_event = next(e for e in events if e["event_type"] == "mission_state_updated")
    assert mission_event["turn_index"] == 3


def test_debug_metadata_records_configured_provider_not_internal_format(tmp_path: Path) -> None:
    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "prov", "obj", tmp_path, "execution", "m", provider="openai")
    session_meta = json.loads((tmp_path / "prov" / "session_meta.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (tmp_path / "prov" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    run_started = next(e for e in events if e["event_type"] == "run_started")

    assert session_meta["provider"] == "openai"
    assert run_started["payload"]["provider"] == "openai"
