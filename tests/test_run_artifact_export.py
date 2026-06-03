from __future__ import annotations

import json
from pathlib import Path

import pytest

from villani_code.execution import ExecutionBudget
from villani_code.run_artifacts import append_jsonl, canonical_artifact_dir, usage_from_events, write_full_transcript, write_json, write_trajectory
from villani_code.state import Runner


class _SequenceClient:
    def __init__(self, responses: list[dict] | None = None, exc: Exception | None = None, exc_after: int | None = None):
        self.responses = responses or []
        self.exc = exc
        self.exc_after = exc_after
        self.payloads: list[dict] = []

    def create_message(self, payload, stream):
        self.payloads.append(payload)
        if self.exc is not None and (self.exc_after is None or len(self.payloads) > self.exc_after):
            raise self.exc
        return self.responses.pop(0)


def _mission_dir(repo: Path) -> Path:
    return canonical_artifact_dir(repo)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _required_artifacts(run_dir: Path) -> set[str]:
    return {"telemetry.json", "full_transcript.json", "trajectory.json", "runtime_events.jsonl", "model_requests.jsonl", "model_responses.jsonl", "run_meta.json"}


def _assert_required(run_dir: Path) -> None:
    assert _required_artifacts(run_dir).issubset({p.name for p in run_dir.iterdir()})


def test_ordinary_success_emits_required_artifacts_and_exact_usage(tmp_path: Path) -> None:
    client = _SequenceClient([
        {"content": [{"type": "text", "text": "I will run it."}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo ok"}}], "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}},
        {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 6, "total_tokens": 11}},
    ])
    result = Runner(client=client, repo=tmp_path, model="model-x", provider="provider-y", stream=False).run("do it")
    run_dir = _mission_dir(tmp_path)
    _assert_required(run_dir)
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
    assert trajectory["agent"] == {"name": "villani-code", "version": None, "model_name": "model-x", "extra": {"provider": "provider-y"}}
    assert trajectory["final_metrics"] == {"total_prompt_tokens": 8, "total_completion_tokens": 10, "total_steps": len(trajectory["steps"])}
    tool_steps = [step for step in trajectory["steps"] if step.get("tool_calls")]
    assert len(tool_steps) == 1
    tool_step = tool_steps[0]
    assert tool_step["message"] == "I will run it."
    assert tool_step["metrics"] == {"prompt_tokens": 3, "completion_tokens": 4}
    assert tool_step["tool_calls"] == [{"tool_call_id": "t1", "function_name": "Bash", "arguments": {"command": "echo ok"}}]
    assert tool_step["observation"]["results"][0]["tool_call_id"] == "t1"
    assert "ok" in tool_step["observation"]["results"][0]["content"]
    assert result["response"]["content"][0]["text"] == "done"


def test_token_usage_policy_requires_every_completed_response_exact() -> None:
    exact = {"event_type": "model_response", "usage_quality": "exact", "input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
    exact2 = {"event_type": "model_response", "usage_quality": "exact", "input_tokens": 5, "output_tokens": 6, "total_tokens": 11}
    missing = {"event_type": "model_response", "usage_quality": "unavailable", "input_tokens": None, "output_tokens": None, "total_tokens": None}
    failed = {"event_type": "model_exception", "exception_type": "RuntimeError"}
    assert usage_from_events([exact, exact2]) == {"input_tokens": 8, "output_tokens": 10, "total_tokens": 18, "quality": "exact"}
    assert usage_from_events([exact, missing]) == {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
    assert usage_from_events([]) == {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
    assert usage_from_events([failed]) == {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
    assert usage_from_events([exact, failed]) == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7, "quality": "exact"}


def test_missing_usage_is_unavailable_not_estimated(tmp_path: Path) -> None:
    client = _SequenceClient([
        {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo ok"}}], "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}},
        {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"},
    ])
    Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False).run("mixed usage")
    telemetry = _load(_mission_dir(tmp_path) / "telemetry.json")
    assert telemetry["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}


def test_timeout_path_emits_partial_artifacts(tmp_path: Path) -> None:
    client = _SequenceClient([{"content": [{"type": "text", "text": "more"}], "stop_reason": "end_turn"}])
    Runner(client=client, repo=tmp_path, model="m", stream=False).run("timeout", execution_budget=ExecutionBudget(max_seconds=-1, max_turns=10, max_tool_calls=10, max_no_edit_turns=10, max_reconsecutive_recon_turns=10))
    run_dir = _mission_dir(tmp_path)
    _assert_required(run_dir)
    telemetry = _load(run_dir / "telemetry.json")
    assert telemetry["outcome"]["verified_outcome"] == "timed_out"
    assert telemetry["termination"]["timed_out"] is True
    trajectory = _load(run_dir / "trajectory.json")
    assert trajectory["extra"]["verified_outcome"] == "timed_out"
    assert trajectory["extra"]["termination_reason"] == "max_seconds"
    assert trajectory["steps"][-1]["message"] == "more"


def test_model_exception_after_completed_response_emits_partial_artifacts_and_raises_same_exception(tmp_path: Path) -> None:
    exc = RuntimeError("bad api_key=super-secret-value")
    client = _SequenceClient(
        responses=[{"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo ok"}}], "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}}],
        exc=exc,
        exc_after=1,
    )
    with pytest.raises(RuntimeError) as raised:
        Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False).run("explode later")
    assert raised.value is exc
    run_dir = _mission_dir(tmp_path)
    _assert_required(run_dir)
    telemetry = _load(run_dir / "telemetry.json")
    assert telemetry["usage"] == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7, "quality": "exact"}
    assert telemetry["outcome"]["verified_outcome"] == "exception"
    assert telemetry["termination"]["exception_type"] == "RuntimeError"
    assert "super-secret" not in json.dumps(telemetry)
    transcript = _load(run_dir / "full_transcript.json")
    assert transcript["events"][-1]["type"] == "terminal_state"
    trajectory = _load(run_dir / "trajectory.json")
    assert trajectory["extra"]["verified_outcome"] == "exception"
    assert trajectory["steps"][1]["tool_calls"][0]["tool_call_id"] == "t1"


def test_tool_runtime_exception_outside_model_path_emits_partial_artifacts_and_raises_same_exception(tmp_path: Path, monkeypatch) -> None:
    import villani_code.state_tooling as state_tooling

    exc = ValueError("tool password=hunter2-secret")
    client = _SequenceClient([{"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "boom"}}], "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}])

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(state_tooling, "execute_tool_with_policy", boom)
    with pytest.raises(ValueError) as raised:
        Runner(client=client, repo=tmp_path, model="m", stream=False).run("tool explode")
    assert raised.value is exc
    run_dir = _mission_dir(tmp_path)
    _assert_required(run_dir)
    trajectory = _load(run_dir / "trajectory.json")
    tool_step = [step for step in trajectory["steps"] if step.get("tool_calls")][0]
    assert "observation" not in tool_step
    assert trajectory["extra"]["exception_type"] == "ValueError"
    assert "hunter2" not in json.dumps(_load(run_dir / "telemetry.json"))


def test_two_runs_are_isolated_and_no_root_runtime_events(tmp_path: Path) -> None:
    Runner(client=_SequenceClient([{"content": [{"type": "text", "text": "one"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}]), repo=tmp_path, model="m", stream=False).run("first")
    first = _mission_dir(tmp_path)
    Runner(client=_SequenceClient([{"content": [{"type": "text", "text": "two"}], "stop_reason": "end_turn", "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}]), repo=tmp_path, model="m", stream=False).run("second")
    second = _mission_dir(tmp_path)
    assert first != second
    assert _load(first / "telemetry.json")["usage"]["total_tokens"] == 2
    assert _load(second / "telemetry.json")["usage"]["total_tokens"] == 5
    first_transcript = _load(first / "full_transcript.json")
    second_transcript = _load(second / "full_transcript.json")
    assert first_transcript["run_id"] != second_transcript["run_id"]
    assert first_transcript["events"][0]["content"] == "first"
    assert second_transcript["events"][0]["content"] == "second"
    assert not (tmp_path / ".villani_code" / "runtime_events.jsonl").exists()
    assert canonical_artifact_dir(tmp_path) == second


def test_atif_multiple_tool_calls_correlates_observations_and_metrics(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    append_jsonl(run_dir / "model_responses.jsonl", {"ts": "2", "event_type": "model_response", "request_id": "r1", "usage_quality": "exact", "input_tokens": 3, "output_tokens": 4, "total_tokens": 7, "payload": {"content": [{"type": "text", "text": "I will run tools."}, {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "echo a"}}, {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "echo b"}}]}})
    append_jsonl(run_dir / "events.jsonl", {"ts": "3", "event_type": "tool_call_completed", "payload": {"tool_call_id": "a", "result": {"stdout": "out-a"}}})
    append_jsonl(run_dir / "events.jsonl", {"ts": "4", "event_type": "tool_call_completed", "payload": {"tool_call_id": "b", "result": {"stdout": "out-b"}}})
    write_json(run_dir / "telemetry.json", {"usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7, "quality": "exact"}})
    write_full_transcript(run_dir, run_id="run", instruction="do")
    trajectory_path = write_trajectory(run_dir, run_id="run", mission_id="run", agent_version=None, model="m", provider="p")
    trajectory = _load(trajectory_path)
    step = [s for s in trajectory["steps"] if s.get("tool_calls")][0]
    assert step["message"] == "I will run tools."
    assert step["metrics"] == {"prompt_tokens": 3, "completion_tokens": 4}
    assert [c["tool_call_id"] for c in step["tool_calls"]] == ["a", "b"]
    assert {r["tool_call_id"]: r["content"] for r in step["observation"]["results"]} == {"a": "out-a", "b": "out-b"}
    assert trajectory["final_metrics"]["total_prompt_tokens"] == 3
    assert trajectory["final_metrics"]["total_completion_tokens"] == 4


def test_redacts_fake_secrets_in_persisted_artifacts(tmp_path: Path) -> None:
    secret = "sk-testSECRET1234567890"
    client = _SequenceClient([{"content": [{"type": "text", "text": f"Authorization: Bearer {secret}\nOPENAI_API_KEY={secret}"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "client_secret": secret}])
    Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False).run(f"use api_key={secret}")
    run_dir = _mission_dir(tmp_path)
    for name in ["full_transcript.json", "trajectory.json", "runtime_events.jsonl", "model_requests.jsonl", "model_responses.jsonl", "telemetry.json", "run_meta.json"]:
        text = (run_dir / name).read_text(encoding="utf-8")
        assert secret not in text, name
        if name not in {"telemetry.json", "run_meta.json"}:
            assert "[REDACTED_SECRET]" in text, name


def test_existing_verification_failure_maps_failed_without_runner_outcome_change(tmp_path: Path) -> None:
    from villani_code.debug_mode import build_debug_config
    from villani_code.debug_recorder import DebugRecorder

    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "run-fail", "objective", tmp_path, "execution", "m", "p")
    recorder.record_validation_start("post_execution", {"command": "existing verifier"})
    recorder.record_validation_finish("post_execution", 1, "failed")
    recorder.write_final_summary(status="completed", termination_reason="completed", total_turns=0, mission_id="run-fail")
    telemetry = _load(tmp_path / "run-fail" / "telemetry.json")
    assert telemetry["outcome"]["verified_outcome"] == "failed"
