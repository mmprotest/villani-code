from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from villani_code.debug_mode import DebugConfig, DebugMode
from villani_code.evidence_loop import (
    EvidenceLoopState,
    build_intervention_message,
    completion_is_allowed,
    invoke_semantic_evaluator,
    parse_evaluation_payload,
    record_intervention,
    record_observation,
    record_tool_result,
)
from villani_code.state import Runner


def _json_eval(status: str, mode: str, support: list[str] | None = None, missing: list[str] | None = None, supporting_ids: list[str] | None = None, contradicting_ids: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    payload = {
        "status": status,
        "goal_alignment_summary": extra.pop("goal_alignment_summary", "semantic assessment"),
        "supporting_evidence": support or [],
        "contradicting_evidence": extra.pop("contradicting_evidence", []),
        "supporting_observation_ids": supporting_ids or [],
        "contradicting_observation_ids": contradicting_ids or [],
        "missing_evidence": missing or [],
        "active_blocker": extra.pop("active_blocker", None),
        "unsupported_claims": extra.pop("unsupported_claims", []),
        "required_next_mode": mode,
        "reason": extra.pop("reason", "bounded evaluator result"),
    }
    payload.update(extra)
    return {"role": "assistant", "content": [{"type": "text", "text": json.dumps(payload)}]}


class EvalClient:
    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create_message(self, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        self.calls.append(payload)
        if not self.responses:
            raise AssertionError("unexpected evaluator call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _observation_ids_from_evaluator_payload(payload: dict[str, Any]) -> list[str]:
    text = payload["messages"][0]["content"][0]["text"]
    marker = "Evaluation context JSON:\n"
    context = json.loads(text.split(marker, 1)[1])
    return [item["observation_id"] for item in context["recent_observations_neutral"]]


def test_successful_operational_action_is_neutral_not_completion_evidence() -> None:
    state = EvidenceLoopState(current_goal="achieve requested outcome")
    record_tool_result(state, tool_name="Write", tool_input={"file_path": "neutral", "content": "x"}, result={"content": "operation succeeded", "is_error": False}, turn_index=1)

    assert state.raw_action_count == 1
    assert state.recent_actions[-1].succeeded_operationally is True
    assert state.completion_supporting_evidence == []
    assert state.last_evaluation is None


def test_successful_irrelevant_observation_is_neutral_until_evaluator_approves() -> None:
    state = EvidenceLoopState(current_goal="answer the requested question")
    record_observation(state, source="generic-inspection", observation_summary="unrelated object exists", turn_index=1)

    client = EvalClient(_json_eval("unverified", "gather_completion_evidence", missing=["Need evidence tied to the requested question."]))
    evaluation = invoke_semantic_evaluator(client, "m", state, trigger="completion", attempted_completion="Done")

    assert state.raw_observation_count == 1
    assert state.completion_supporting_evidence == []
    assert not completion_is_allowed(evaluation, state)


def test_inconclusive_observation_is_never_positive_evidence() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")
    record_observation(state, source="side-check", observation_summary="inconclusive; unable to determine result", operational_error="inconclusive", turn_index=1)

    client = EvalClient(_json_eval("unverified", "gather_completion_evidence", missing=["The only observation is inconclusive."]))
    evaluation = invoke_semantic_evaluator(client, "m", state, trigger="completion", attempted_completion="Complete")

    assert state.completion_supporting_evidence == []
    assert state.active_blocker is None or "inconclusive" in state.unresolved_uncertainties[-1]
    assert not completion_is_allowed(evaluation, state)


def test_completion_with_no_semantic_support_is_rejected_after_actions() -> None:
    state = EvidenceLoopState(current_goal="produce result")
    record_tool_result(state, tool_name="Write", tool_input={"file_path": "artifact", "content": "x"}, result={"content": "ok", "is_error": False}, turn_index=1)

    evaluation = invoke_semantic_evaluator(
        EvalClient(_json_eval("unverified", "gather_completion_evidence", missing=["No relevant observation of the requested result."])),
        "m",
        state,
        trigger="completion",
        attempted_completion="I finished.",
    )

    assert state.completion_status == "unsupported"
    assert not completion_is_allowed(evaluation, state)


def test_zero_material_action_completion_is_still_evaluated_and_rejected() -> None:
    state = EvidenceLoopState(current_goal="decide from evidence")

    evaluation = invoke_semantic_evaluator(
        EvalClient(_json_eval("unverified", "gather_completion_evidence", missing=["No evidence was gathered."])),
        "m",
        state,
        trigger="completion",
        attempted_completion="The answer is yes.",
    )

    assert state.raw_action_count == 0
    assert state.completion_status == "unsupported"
    assert not completion_is_allowed(evaluation, state)


def test_completion_allowed_only_with_ready_finish_and_nonempty_support() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")
    record_observation(state, source="generic-observation", observation_summary="relevant observed outcome", turn_index=1)

    evaluation = invoke_semantic_evaluator(
        EvalClient(_json_eval("ready_to_finish", "finish", support=["The observed outcome directly supports the final claim."], supporting_ids=[state.recent_observations[-1].observation_id])),
        "m",
        state,
        trigger="completion",
        attempted_completion="Completed based on observed outcome.",
    )

    assert completion_is_allowed(evaluation, state)
    assert state.completion_status == "supported"
    assert state.completion_supporting_evidence == ["The observed outcome directly supports the final claim."]


def test_ready_finish_without_support_is_rejected() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")
    evaluation = invoke_semantic_evaluator(EvalClient(_json_eval("ready_to_finish", "finish", support=[])), "m", state, trigger="completion", attempted_completion="Done")

    assert not completion_is_allowed(evaluation, state)
    assert state.completion_status == "unsupported"


def test_free_text_support_without_observation_ids_is_rejected() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")
    record_observation(state, source="generic-observation", observation_summary="relevant raw observation", turn_index=1)

    evaluation = invoke_semantic_evaluator(
        EvalClient(_json_eval("ready_to_finish", "finish", support=["Free-text support only."])),
        "m",
        state,
        trigger="completion",
        attempted_completion="Done",
    )

    assert not completion_is_allowed(evaluation, state)
    assert state.completion_status == "unsupported"
    assert "valid recorded supporting observation IDs" in state.completion_missing_evidence[-1]


def test_unknown_supporting_observation_id_is_rejected_and_recorded() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")
    record_observation(state, source="generic-observation", observation_summary="relevant raw observation", turn_index=1)

    evaluation = invoke_semantic_evaluator(
        EvalClient(_json_eval("ready_to_finish", "finish", support=["Cites an unknown observation."], supporting_ids=["obs-missing"])),
        "m",
        state,
        trigger="completion",
        attempted_completion="Done",
    )

    assert not completion_is_allowed(evaluation, state)
    assert state.completion_status == "unsupported"
    assert any("unknown observation_id" in item["error"] for item in state.evaluator_failures)
    assert state.evaluator_failures[-1]["invalid_observation_ids"] == ["obs-missing"]


def test_valid_observation_id_is_preserved_in_telemetry() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")
    record_observation(state, source="generic-observation", observation_summary="relevant raw observation", turn_index=1)
    observation_id = state.recent_observations[-1].observation_id

    evaluation = invoke_semantic_evaluator(
        EvalClient(_json_eval("ready_to_finish", "finish", support=["Cites valid observation."], supporting_ids=[observation_id])),
        "m",
        state,
        trigger="completion",
        attempted_completion="Done",
    )

    telemetry = state.to_dict()
    assert completion_is_allowed(evaluation, state)
    assert telemetry["recent_observations"][-1]["observation_id"] == observation_id
    assert telemetry["evaluator_outputs"][-1]["evaluation"]["supporting_observation_ids"] == [observation_id]


def test_malformed_evaluator_output_defaults_conservatively() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")
    malformed = {"role": "assistant", "content": [{"type": "text", "text": "not json"}]}

    evaluation = invoke_semantic_evaluator(EvalClient(malformed), "m", state, trigger="completion", attempted_completion="Done")

    assert evaluation.status == "unverified"
    assert evaluation.required_next_mode == "gather_completion_evidence"
    assert state.evaluator_failures
    assert not completion_is_allowed(evaluation, state)


def test_evaluator_failure_defaults_conservatively() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")

    evaluation = invoke_semantic_evaluator(EvalClient(RuntimeError("timeout")), "m", state, trigger="completion", attempted_completion="Done")

    assert evaluation.status == "unverified"
    assert "timeout" in evaluation.reason
    assert state.evaluator_failures
    assert not completion_is_allowed(evaluation, state)


def test_raw_records_are_distinct_from_evaluator_approved_support() -> None:
    state = EvidenceLoopState(current_goal="complete outcome")
    record_tool_result(state, tool_name="GenericTool", tool_input={"name": "raw"}, result={"content": "raw observation", "is_error": False}, turn_index=1)
    evaluation = invoke_semantic_evaluator(
        EvalClient(_json_eval("ready_to_finish", "finish", support=["Semantic evaluator selected this relevant observation."], supporting_ids=[state.recent_observations[-1].observation_id])),
        "m",
        state,
        trigger="completion",
        attempted_completion="Done",
    )

    assert state.recent_observations[-1].observation_summary == "raw observation"
    assert state.completion_supporting_evidence != [state.recent_observations[-1].observation_summary]
    assert completion_is_allowed(evaluation, state)


def test_intervention_uses_evaluator_required_mode_not_tool_or_command_pattern() -> None:
    state = EvidenceLoopState(current_goal="advance generic task")
    record_tool_result(state, tool_name="Bash", tool_input={"command": "opaque command text"}, result={"content": "operation succeeded", "is_error": False}, turn_index=1)
    evaluation = invoke_semantic_evaluator(
        EvalClient(_json_eval("unverified", "observe_result", missing=["Need observed consequence."])),
        "m",
        state,
        trigger="trajectory",
    )
    message = build_intervention_message(evaluation, trigger="trajectory", state=state)
    record_intervention(state, turn_id=1, kind=evaluation.required_next_mode, message=message, evaluation=evaluation)

    assert "most informative available method" in message
    assert "opaque command text" not in message
    assert state.interventions[-1].kind == "observe_result"


def test_parser_rejects_invalid_schema() -> None:
    try:
        parse_evaluation_payload(json.dumps({"status": "ready_to_finish", "required_next_mode": "finish", "supporting_evidence": "not-list"}))
    except ValueError as exc:
        assert "list" in str(exc) or "must" in str(exc)
    else:
        raise AssertionError("invalid schema should be rejected")


def test_adversarial_mutation_then_unrelated_success_still_rejected() -> None:
    state = EvidenceLoopState(current_goal="specific requested outcome")
    record_tool_result(state, tool_name="Write", tool_input={"file_path": "target", "content": "changed"}, result={"content": "ok", "is_error": False}, turn_index=1)
    record_tool_result(state, tool_name="GenericTool", tool_input={"name": "unrelated"}, result={"content": "unrelated success", "is_error": False}, turn_index=2)

    evaluation = invoke_semantic_evaluator(EvalClient(_json_eval("unverified", "gather_completion_evidence", missing=["Unrelated success does not support the requested outcome."])), "m", state, trigger="completion", attempted_completion="Done")

    assert not completion_is_allowed(evaluation, state)
    assert state.completion_supporting_evidence == []


def test_adversarial_changed_artifact_then_unrelated_observation_rejected() -> None:
    state = EvidenceLoopState(current_goal="observe changed artifact")
    record_tool_result(state, tool_name="Write", tool_input={"file_path": "artifact-a", "content": "new"}, result={"content": "ok", "is_error": False}, turn_index=1)
    record_observation(state, source="generic-observation", observation_summary="artifact-b is readable", turn_index=2)

    evaluation = invoke_semantic_evaluator(EvalClient(_json_eval("unverified", "gather_completion_evidence", missing=["Observation was about an unrelated artifact."])), "m", state, trigger="completion", attempted_completion="Done")

    assert not completion_is_allowed(evaluation, state)


def test_adversarial_inconclusive_then_completion_rejected() -> None:
    state = EvidenceLoopState(current_goal="confirm outcome")
    record_observation(state, source="generic-check", observation_summary="could not determine whether outcome occurred", operational_error="inconclusive", turn_index=1)

    evaluation = invoke_semantic_evaluator(EvalClient(_json_eval("unverified", "gather_completion_evidence", missing=["The observation is inconclusive."])), "m", state, trigger="completion", attempted_completion="Confirmed")

    assert not completion_is_allowed(evaluation, state)


def test_adversarial_no_mutation_unsupported_conclusion_rejected() -> None:
    state = EvidenceLoopState(current_goal="make a supported conclusion")

    evaluation = invoke_semantic_evaluator(EvalClient(_json_eval("unverified", "gather_completion_evidence", missing=["No supporting evidence for the conclusion."])), "m", state, trigger="completion", attempted_completion="Unsupported conclusion")

    assert not completion_is_allowed(evaluation, state)


def test_adversarial_partial_despite_multiple_successful_tools_rejected() -> None:
    state = EvidenceLoopState(current_goal="specific outcome")
    for turn in range(3):
        record_tool_result(state, tool_name="GenericTool", tool_input={"turn": turn}, result={"content": "success", "is_error": False}, turn_index=turn)

    evaluation = invoke_semantic_evaluator(EvalClient(_json_eval("progressing", "gather_completion_evidence", support=["Some generic activity occurred."], missing=["Still missing evidence for the requested outcome."])), "m", state, trigger="completion", attempted_completion="Done")

    assert not completion_is_allowed(evaluation, state)
    assert state.completion_status == "unsupported"


def test_adversarial_supported_relevant_observation_allowed() -> None:
    state = EvidenceLoopState(current_goal="specific outcome")
    record_observation(state, source="generic-observation", observation_summary="semantically relevant observed outcome", turn_index=1)

    evaluation = invoke_semantic_evaluator(EvalClient(_json_eval("ready_to_finish", "finish", support=["Semantically relevant observed outcome supports the final claim."], supporting_ids=[state.recent_observations[-1].observation_id])), "m", state, trigger="completion", attempted_completion="Done")

    assert completion_is_allowed(evaluation, state)


class EvidenceAwareRunnerClient:
    def __init__(self) -> None:
        self.main_calls = 0
        self.eval_calls = 0
        self.payloads: list[dict[str, Any]] = []

    def create_message(self, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        self.payloads.append(payload)
        prompt_text = json.dumps(payload)
        if "bounded semantic evidence evaluator" in prompt_text:
            self.eval_calls += 1
            if self.eval_calls == 1:
                return _json_eval("unverified", "gather_completion_evidence", missing=["Need an observation tied to the requested outcome."], unsupported_claims=["The final claim is not yet evidenced."])
            ids = _observation_ids_from_evaluator_payload(payload)
            return _json_eval("ready_to_finish", "finish", support=["The later observation semantically supports the requested outcome."], supporting_ids=[ids[-1]])
        self.main_calls += 1
        if self.main_calls == 1:
            return {"role": "assistant", "content": [{"type": "tool_use", "id": "work", "name": "Write", "input": {"file_path": "artifact.txt", "content": "result"}}]}
        if self.main_calls == 2:
            return {"role": "assistant", "content": [{"type": "text", "text": "Done without evidence."}]}
        if self.main_calls == 3:
            assert "Completion is not yet supported" in prompt_text
            return {"role": "assistant", "content": [{"type": "tool_use", "id": "observe", "name": "Read", "input": {"file_path": "artifact.txt"}}]}
        return {"role": "assistant", "content": [{"type": "text", "text": "Done with observed evidence."}]}


def test_runner_rejects_unsupported_completion_then_allows_semantic_support(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    client = EvidenceAwareRunnerClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, debug_config=DebugConfig(mode=DebugMode.NORMAL, debug_root=debug_root))

    result = runner.run("Complete a generic requested outcome with evidence.")

    state = result["transcript"]["evidence_loop"]
    assert state["completion_status"] == "supported"
    assert state["completion_supporting_evidence"] == ["The later observation semantically supports the requested outcome."]
    assert any(item["kind"] == "completion_rejected" for item in state["interventions"])
    run_dir = next(debug_root.iterdir())
    assert (run_dir / "evidence_loop.jsonl").exists()
    assert (run_dir / "evidence_loop_state.json").exists()


class StalledRunnerClient:
    def __init__(self) -> None:
        self.main_calls = 0
        self.eval_calls = 0
        self.payloads: list[dict[str, Any]] = []

    def create_message(self, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        self.payloads.append(payload)
        prompt_text = json.dumps(payload)
        if "bounded semantic evidence evaluator" in prompt_text:
            self.eval_calls += 1
            if self.eval_calls == 1:
                return _json_eval("unverified", "observe_result", missing=["No demonstrated consequence of the repeated work."])
            ids = _observation_ids_from_evaluator_payload(payload)
            return _json_eval("ready_to_finish", "finish", support=["Observation after redirect supports the narrowed final claim."], supporting_ids=[ids[-1]])
        self.main_calls += 1
        if self.main_calls in {1, 2}:
            return {"role": "assistant", "content": [{"type": "tool_use", "id": f"work-{self.main_calls}", "name": "Write", "input": {"file_path": f"artifact-{self.main_calls}.txt", "content": "x"}}]}
        if self.main_calls == 3:
            assert "most informative available method" in prompt_text
            assert "run" not in prompt_text.lower() or "particular" not in prompt_text.lower()
            return {"role": "assistant", "content": [{"type": "tool_use", "id": "observe", "name": "Read", "input": {"file_path": "artifact-2.txt"}}]}
        return {"role": "assistant", "content": [{"type": "text", "text": "Finished after observing."}]}


def test_runner_injects_general_observe_redirect_when_progress_unverified(tmp_path: Path) -> None:
    client = StalledRunnerClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    result = runner.run("Perform generic work and use evidence.")

    interventions = result["transcript"]["evidence_loop"]["interventions"]
    assert any(item["kind"] == "observe_result" for item in interventions)
    assert all("pytest" not in item["message"] and "build" not in item["message"] for item in interventions)
