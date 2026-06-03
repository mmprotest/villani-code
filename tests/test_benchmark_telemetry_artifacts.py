from __future__ import annotations

import json
from pathlib import Path

from villani_code.benchmark.agents.villani import VillaniAgentRunner
from villani_code.benchmark.telemetry import finalize_attempt_artifacts, read_jsonl


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_provider_usage_and_elapsed_are_aggregated_into_telemetry(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    attempt = tmp_path / "agent_debug" / "task__r0"
    debug_run = attempt / "mission-1"
    _write_jsonl(debug_run / "model_responses.jsonl", [
        {"payload": {"request_id": "mr-1", "model": "m", "provider": "p", "elapsed_seconds": 1.5, "usage": {"input_tokens": 3, "output_tokens": 4}, "stop_reason": "end_turn"}},
        {"payload": {"request_id": "mr-2", "model": "m", "provider": "p", "elapsed_seconds": 2.0, "usage": {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}, "stop_reason": "end_turn"}},
    ])
    _write_jsonl(debug_run / "model_requests.jsonl", [{"payload": {"request_id": "mr-1", "model": "m"}}])
    (debug_run / "full_transcript.json").write_text(json.dumps({"instruction": "fix", "responses": [{"content": [{"type": "text", "text": "done"}], "usage": {"input_tokens": 3, "output_tokens": 4}}], "tool_results": []}), encoding="utf-8")

    telemetry = finalize_attempt_artifacts(
        attempt_dir=attempt,
        repo=repo,
        task_id="task",
        repeat_index=0,
        model="m",
        provider="p",
        agent_version="1",
        agent_process_elapsed_seconds=10.0,
        total_attempt_duration_seconds=12.0,
        verified_outcome="passed",
        visible_pass=True,
        hidden_pass=True,
        timed_out=False,
        termination_reason=None,
    )

    assert telemetry["usage"] == {"input_tokens": 8, "output_tokens": 10, "total_tokens": 18, "quality": "exact"}
    assert telemetry["model"] == {"identifier": "m", "provider": "p"}
    assert telemetry["timing"]["local_inference_elapsed_seconds"] == 3.5
    assert all((attempt / name).exists() for name in ["telemetry.json", "full_transcript.json", "trajectory.json", "events.jsonl", "model_requests.jsonl", "model_responses.jsonl", "agent_stdout.txt", "agent_stderr.txt", "agent_run_meta.json"])
    traj = json.loads((attempt / "trajectory.json").read_text(encoding="utf-8"))
    assert traj["schema_version"] == "ATIF-v1.7"
    assert traj["agent"]["name"] == "villani"
    assert [step["step_id"] for step in traj["steps"]] == list(range(1, len(traj["steps"]) + 1))
    assert traj["steps"][0]["source"] == "user"
    assert traj["final_metrics"]["total_prompt_tokens"] == 8
    assert traj["final_metrics"]["total_completion_tokens"] == 10


def test_missing_usage_is_unavailable_and_tokens_null(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    attempt = tmp_path / "agent_debug" / "task__r0"
    debug_run = attempt / "mission-1"
    _write_jsonl(debug_run / "model_responses.jsonl", [{"payload": {"request_id": "mr-1", "elapsed_seconds": 0.25}}])
    telemetry = finalize_attempt_artifacts(
        attempt_dir=attempt,
        repo=repo,
        task_id="task",
        repeat_index=0,
        model="m",
        provider="p",
        agent_version="1",
        agent_process_elapsed_seconds=None,
        total_attempt_duration_seconds=None,
        verified_outcome="exception",
        visible_pass=False,
        hidden_pass=False,
        timed_out=False,
        termination_reason="benchmark_error",
        exception_type="RuntimeError",
        exception_message="boom",
    )
    assert telemetry["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
    assert telemetry["termination"]["exception_type"] == "RuntimeError"


def test_runtime_event_discovery_uses_current_mission(tmp_path: Path) -> None:
    mission = tmp_path / ".villani_code" / "missions" / "mission-2"
    mission.mkdir(parents=True)
    (tmp_path / ".villani_code" / "missions" / "current.json").write_text(json.dumps({"mission_id": "mission-2"}), encoding="utf-8")
    (mission / "runtime_events.jsonl").write_text('{"type":"tool_finished","payload":{"type":"tool_finished","name":"Read"}}\n', encoding="utf-8")
    legacy = tmp_path / ".villani_code" / "runtime_events.jsonl"
    legacy.write_text('{"type":"stale"}\n', encoding="utf-8")
    events_file = VillaniAgentRunner._runtime_events_file_for_current_mission(tmp_path)
    assert events_file == mission / "runtime_events.jsonl"


def test_artifacts_redact_sensitive_credentials_and_timeout_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    attempt = tmp_path / "agent_debug" / "task__r0"
    debug_run = attempt / "mission-1"
    (debug_run).mkdir(parents=True)
    (debug_run / "full_transcript.json").write_text(json.dumps({"instruction": "use api_key=sk-secret", "requests": [{"api_key": "sk-secret"}], "responses": []}), encoding="utf-8")
    telemetry = finalize_attempt_artifacts(
        attempt_dir=attempt,
        repo=repo,
        task_id="task",
        repeat_index=0,
        model="m",
        provider="p",
        agent_version="1",
        agent_process_elapsed_seconds=60.0,
        total_attempt_duration_seconds=61.0,
        verified_outcome="timed_out",
        visible_pass=False,
        hidden_pass=False,
        timed_out=True,
        termination_reason="agent_timeout",
    )
    transcript_text = (attempt / "full_transcript.json").read_text(encoding="utf-8")
    assert "sk-secret" not in transcript_text
    assert telemetry["termination"]["timed_out"] is True
    assert telemetry["outcome"]["verified_outcome"] == "timed_out"
