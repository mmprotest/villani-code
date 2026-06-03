from __future__ import annotations

import json
from pathlib import Path

import pytest

from villani_code.execution import ExecutionBudget
from villani_code.run_artifacts import canonical_artifact_dir
from villani_code.state import Runner


class _SequenceClient:
    def __init__(self, responses: list[dict] | None = None, exc: Exception | None = None):
        self.responses = responses or []
        self.exc = exc
        self.payloads: list[dict] = []

    def create_message(self, payload, stream):
        self.payloads.append(payload)
        if self.exc is not None:
            raise self.exc
        return self.responses.pop(0)


def _mission_dir(repo: Path) -> Path:
    return canonical_artifact_dir(repo)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_ordinary_success_emits_required_artifacts_and_exact_usage(tmp_path: Path) -> None:
    client = _SequenceClient([
        {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo ok", "authorization": "Bearer secret-token-12345"}}], "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}},
        {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 6, "total_tokens": 11}},
    ])
    result = Runner(client=client, repo=tmp_path, model="model-x", provider="provider-y", stream=False).run("do it")
    run_dir = _mission_dir(tmp_path)
    required = {"telemetry.json", "full_transcript.json", "trajectory.json", "runtime_events.jsonl", "model_requests.jsonl", "model_responses.jsonl", "run_meta.json"}
    assert required.issubset({p.name for p in run_dir.iterdir()})
    telemetry = _load(run_dir / "telemetry.json")
    assert telemetry["model"] == {"identifier": "model-x", "provider": "provider-y"}
    assert telemetry["usage"] == {"input_tokens": 8, "output_tokens": 10, "total_tokens": 18, "quality": "exact"}
    assert telemetry["timing"]["local_inference_elapsed_seconds"] >= 0
    assert telemetry["timing"]["total_attempt_duration_seconds"] >= telemetry["timing"]["local_inference_elapsed_seconds"]
    assert _load(run_dir / "run_meta.json")["model_identifier"] == "model-x"
    transcript = _load(run_dir / "full_transcript.json")
    kinds = [e["type"] for e in transcript["events"]]
    assert kinds.index("user_instruction") < kinds.index("model_request") < kinds.index("assistant_response") < kinds.index("tool_invocation") < kinds.index("tool_observation") < kinds.index("terminal_state")
    trajectory = _load(run_dir / "trajectory.json")
    assert trajectory["schema_version"] == "ATIF-v1.7"
    assert trajectory["metrics"]["usage"] == telemetry["usage"]
    serialized = json.dumps(transcript) + json.dumps(trajectory)
    assert "secret-token" not in serialized
    assert result["response"]["content"][0]["text"] == "done"


def test_missing_usage_is_unavailable_not_estimated(tmp_path: Path) -> None:
    client = _SequenceClient([{"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}])
    Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False).run("no usage")
    telemetry = _load(_mission_dir(tmp_path) / "telemetry.json")
    assert telemetry["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}


def test_timeout_path_emits_partial_artifacts(tmp_path: Path) -> None:
    client = _SequenceClient([{"content": [{"type": "text", "text": "more"}], "stop_reason": "end_turn"}])
    Runner(client=client, repo=tmp_path, model="m", stream=False).run("timeout", execution_budget=ExecutionBudget(max_seconds=-1, max_turns=10, max_tool_calls=10, max_no_edit_turns=10, max_reconsecutive_recon_turns=10))
    telemetry = _load(_mission_dir(tmp_path) / "telemetry.json")
    assert telemetry["outcome"]["verified_outcome"] == "timed_out"
    assert telemetry["termination"]["timed_out"] is True
    assert (_mission_dir(tmp_path) / "full_transcript.json").exists()


def test_model_exception_path_emits_exception_artifacts(tmp_path: Path) -> None:
    client = _SequenceClient(exc=RuntimeError("bad api_key=super-secret-value"))
    with pytest.raises(RuntimeError):
        Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False).run("explode")
    telemetry = _load(_mission_dir(tmp_path) / "telemetry.json")
    assert telemetry["outcome"]["verified_outcome"] == "exception"
    assert telemetry["termination"]["exception_type"] == "RuntimeError"
    assert "super-secret" not in telemetry["termination"]["exception_message"]
    assert _load(_mission_dir(tmp_path) / "full_transcript.json")["events"][-1]["type"] == "terminal_state"


def test_runtime_artifact_path_resolution_finds_canonical_mission_dir(tmp_path: Path) -> None:
    client = _SequenceClient([{"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}])
    Runner(client=client, repo=tmp_path, model="m", stream=False).run("resolve")
    resolved = canonical_artifact_dir(tmp_path)
    assert resolved.name
    assert (resolved / "runtime_events.jsonl").exists()
    assert (resolved / "telemetry.json").exists()


def test_existing_verification_failure_maps_failed_without_runner_outcome_change(tmp_path: Path) -> None:
    from villani_code.debug_mode import build_debug_config
    from villani_code.debug_recorder import DebugRecorder

    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "run-fail", "objective", tmp_path, "execution", "m", "p")
    recorder.record_validation_start("post_execution", {"command": "existing verifier"})
    recorder.record_validation_finish("post_execution", 1, "failed")
    recorder.write_final_summary(status="completed", termination_reason="completed", total_turns=0, mission_id="run-fail")
    telemetry = _load(tmp_path / "run-fail" / "telemetry.json")
    assert telemetry["outcome"]["verified_outcome"] == "failed"
