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
    assert trajectory["final_metrics"]["total_prompt_tokens"] == telemetry["usage"]["input_tokens"]
    assert trajectory["final_metrics"]["total_completion_tokens"] == telemetry["usage"]["output_tokens"]
    assert trajectory["agent"]["model_name"] == "model-x"
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
    assert not (tmp_path / ".villani_code" / "runtime_events.jsonl").exists()


def test_existing_verification_failure_maps_failed_without_runner_outcome_change(tmp_path: Path) -> None:
    from villani_code.debug_mode import build_debug_config
    from villani_code.debug_recorder import DebugRecorder

    recorder = DebugRecorder(build_debug_config("trace", tmp_path), "run-fail", "objective", tmp_path, "execution", "m", "p")
    recorder.record_validation_start("post_execution", {"command": "existing verifier"})
    recorder.record_validation_finish("post_execution", 1, "failed")
    recorder.write_final_summary(status="completed", termination_reason="completed", total_turns=0, mission_id="run-fail")
    telemetry = _load(tmp_path / "run-fail" / "telemetry.json")
    assert telemetry["outcome"]["verified_outcome"] == "failed"



def test_incomplete_usage_component_makes_aggregate_unavailable(tmp_path: Path) -> None:
    client = _SequenceClient([{"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {"input_tokens": 10}}])
    Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False).run("partial usage")
    assert _load(_mission_dir(tmp_path) / "telemetry.json")["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}


def test_two_missions_have_isolated_artifact_sets(tmp_path: Path) -> None:
    Runner(client=_SequenceClient([{"content": [{"type": "text", "text": "one"}], "stop_reason": "end_turn"}]), repo=tmp_path, model="m", stream=False).run("one")
    first = canonical_artifact_dir(tmp_path)
    Runner(client=_SequenceClient([{"content": [{"type": "text", "text": "two"}], "stop_reason": "end_turn"}]), repo=tmp_path, model="m", stream=False).run("two")
    second = canonical_artifact_dir(tmp_path)
    assert first != second
    assert (first / "telemetry.json").exists()
    assert (second / "telemetry.json").exists()


def test_trajectory_uses_atif_fields_for_messages_tools_and_observations(tmp_path: Path) -> None:
    client = _SequenceClient([
        {"content": [{"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": "echo ok"}}], "usage": {"input_tokens": 1, "output_tokens": 2}},
        {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 4}},
    ])
    Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False).run("tool run")
    telemetry = _load(_mission_dir(tmp_path) / "telemetry.json")
    trajectory = _load(_mission_dir(tmp_path) / "trajectory.json")
    assert set(["schema_version", "session_id", "agent", "steps", "final_metrics", "extra"]).issubset(trajectory)
    assert "model" not in trajectory["agent"]
    assert trajectory["agent"]["model_name"] == "m"
    assert trajectory["final_metrics"]["total_prompt_tokens"] == telemetry["usage"]["input_tokens"] == 4
    assert trajectory["final_metrics"]["total_completion_tokens"] == telemetry["usage"]["output_tokens"] == 6
    step_ids = [step["step_id"] for step in trajectory["steps"]]
    assert step_ids == [f"step-{i}" for i in range(1, len(step_ids) + 1)]
    assert any(step.get("source") == "user" and step.get("message") == "tool run" for step in trajectory["steps"])
    agent_steps = [step for step in trajectory["steps"] if step.get("source") == "agent" and "metrics" in step]
    assert agent_steps[0]["metrics"] == {"prompt_tokens": 1, "completion_tokens": 2}
    tool_step = next(step for step in trajectory["steps"] if step.get("tool_calls"))
    assert tool_step["tool_calls"][0] == {"tool_call_id": "call-1", "function_name": "Bash", "arguments": {"command": "echo ok"}}
    observation_step = next(step for step in trajectory["steps"] if step.get("observation"))
    assert observation_step["observation"]["source_call_id"] == "call-1"
    assert "reasoning" not in json.dumps(trajectory).lower()


def test_recorded_model_call_preserves_payload_invokes_once_and_reraises_same_exception(tmp_path: Path) -> None:
    class Boom(RuntimeError):
        pass
    payload = {"model": "m", "messages": [{"role": "user", "content": []}], "stream": False}
    client = _SequenceClient(exc=Boom("bad"))
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("direct")
    with pytest.raises(Boom) as raised:
        runner._recorded_model_call(payload, stream=False)
    assert raised.value is client.exc
    assert client.payloads == [payload]
    rows = [json.loads(line) for line in (_mission_dir(tmp_path) / "model_responses.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event_type"] == "model_exception"


def test_patch_success_restores_meaningful_edit_recovery_reset(tmp_path: Path, monkeypatch) -> None:
    responses = [
        {"content": [{"type": "text", "text": "I will modify src/app.py."}], "stop_reason": "end_turn"},
        {"content": [{"type": "text", "text": "I will edit src/app.py now."}], "stop_reason": "end_turn"},
        {"content": [{"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "src/app.py", "content": "x"}}], "stop_reason": "tool_use"},
        {"content": [{"type": "text", "text": "I will update src/app.py further."}], "stop_reason": "end_turn"},
        {"content": [{"type": "text", "text": "No further code change is needed; done."}], "stop_reason": "end_turn"},
    ]
    runner = Runner(client=_SequenceClient(responses), repo=tmp_path, model="m", stream=False)
    monkeypatch.setattr(runner, "_run_post_edit_verification", lambda trigger="edit": "")
    monkeypatch.setattr(runner, "_run_patch_effect_check", lambda response, changed_files, objective: "")
    result = runner.run("modify a file")
    assert result["response"]["content"][0]["text"] == "I will update src/app.py further."
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "x"


def _model_response_rows(repo: Path) -> list[dict]:
    return [json.loads(line) for line in (_mission_dir(repo) / "model_responses.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def test_auxiliary_pre_edit_diagnosis_model_call_is_recorded(tmp_path: Path) -> None:
    from villani_code import state_runtime

    client = _SequenceClient([{"content": [{"type": "text", "text": '{"target_file":"src/app.py","bug_class":"logic","fix_intent":"fix"}'}], "usage": {"input_tokens": 7, "output_tokens": 8}}])
    runner = Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False)
    runner._ensure_mission("diagnose")
    diagnosis = state_runtime.run_pre_edit_diagnosis(runner, "diagnose", failure_evidence={"error_summary": "boom"})
    assert diagnosis["target_file"] == "src/app.py"
    assert len(client.payloads) == 1
    rows = _model_response_rows(tmp_path)
    assert rows[-1]["event_type"] == "model_response"
    assert rows[-1]["input_tokens"] == 7


def test_auxiliary_patch_effect_critic_model_call_is_recorded(tmp_path: Path) -> None:
    path = tmp_path / "src" / "app.py"
    path.parent.mkdir()
    path.write_text("print('ok')\n", encoding="utf-8")
    client = _SequenceClient([{"content": [{"type": "text", "text": "YES"}], "usage": {"input_tokens": 9, "output_tokens": 2}}])
    runner = Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False)
    runner._ensure_mission("critic")
    out = runner._run_patch_effect_check({"content": [{"type": "text", "text": "Changed src/app.py to print ok."}]}, ["src/app.py"], "make ok")
    assert out == ""
    assert len(client.payloads) == 1
    assert _model_response_rows(tmp_path)[-1]["output_tokens"] == 2


def test_auxiliary_repair_model_call_is_recorded(tmp_path: Path) -> None:
    from villani_code.repair import RepairContext, _run_repair_prompt

    client = _SequenceClient([{"content": [{"type": "text", "text": "repair done"}], "usage": {"input_tokens": 4, "output_tokens": 5}}])
    runner = Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False)
    runner._ensure_mission("repair")
    text = _run_repair_prompt(runner, RepairContext("task", "plan", "impact", [], "pytest", "failed"), [])
    assert text == "repair done"
    assert len(client.payloads) == 1
    assert _model_response_rows(tmp_path)[-1]["total_tokens"] == 9


def test_multi_model_call_telemetry_sums_auxiliary_and_main_calls(tmp_path: Path) -> None:
    from villani_code import state_runtime

    client = _SequenceClient([
        {"content": [{"type": "text", "text": '{"target_file":"src/app.py","bug_class":"logic","fix_intent":"fix"}'}], "usage": {"input_tokens": 1, "output_tokens": 2}},
        {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 4}},
    ])
    runner = Runner(client=client, repo=tmp_path, model="m", provider="p", stream=False)
    runner._ensure_mission("multi")
    state_runtime.run_pre_edit_diagnosis(runner, "multi", failure_evidence={"error_summary": "boom"})
    runner.run("multi")
    telemetry = _load(_mission_dir(tmp_path) / "telemetry.json")
    assert telemetry["usage"] == {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10, "quality": "exact"}
    assert len(client.payloads) == 2
    assert telemetry["timing"]["local_inference_elapsed_seconds"] >= 0
