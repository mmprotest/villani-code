from __future__ import annotations

import json
from pathlib import Path

from villani_code.debug_mode import DebugConfig, DebugMode
from villani_code.evidence_loop import (
    EvidenceLoopState,
    completion_redirect,
    evaluate_checkpoint,
    evaluate_completion,
    maybe_build_intervention,
    record_tool_result,
)
from villani_code.state import Runner


def test_material_action_followed_by_observation_proceeds_without_intervention() -> None:
    state = EvidenceLoopState(current_goal="produce an artifact")
    record_tool_result(state, tool_name="Write", tool_input={"file_path": "out.txt"}, result={"content": "Wrote out.txt", "is_error": False}, turn_index=1)
    record_tool_result(state, tool_name="Read", tool_input={"file_path": "out.txt"}, result={"content": "expected content", "is_error": False}, turn_index=2)

    assert state.consecutive_actions_without_observation == 0
    assert maybe_build_intervention(state, turn_index=2) is None


def test_multiple_material_actions_without_observation_trigger_redirect() -> None:
    state = EvidenceLoopState(current_goal="advance the task")
    record_tool_result(state, tool_name="Write", tool_input={"file_path": "a.txt"}, result={"content": "ok", "is_error": False}, turn_index=1)
    record_tool_result(state, tool_name="Patch", tool_input={"file_path": "a.txt", "unified_diff": "diff"}, result={"content": "ok", "is_error": False}, turn_index=2)

    redirect = maybe_build_intervention(state, turn_index=2)

    assert redirect is not None
    assert "without obtaining evidence" in redirect
    assert state.evaluator_outputs[-1]["required_next_mode"] == "observe_result"


def test_failed_action_prioritizes_observed_failure_in_recovery_context() -> None:
    state = EvidenceLoopState(current_goal="create file", current_subgoal="write file")
    redirects = record_tool_result(
        state,
        tool_name="Write",
        tool_input={"file_path": "missing/out.txt"},
        result={"content": "parent directory missing", "is_error": True},
        turn_index=1,
    )

    assert redirects
    assert "Observed result" in redirects[0]
    assert "parent directory missing" in redirects[0]
    assert state.active_blocker == "parent directory missing"


def test_repeating_same_failed_approach_triggers_replanning() -> None:
    state = EvidenceLoopState(current_goal="change state")
    for turn in (1, 2):
        record_tool_result(
            state,
            tool_name="Bash",
            tool_input={"command": "make mutate"},
            result={"content": "permission denied", "is_error": True},
            turn_index=turn,
        )

    redirect = maybe_build_intervention(state, turn_index=2)

    assert redirect is not None
    assert "Progress has stalled" in redirect
    assert state.evaluator_outputs[-1]["required_next_mode"] == "replan"


def test_completion_without_supporting_evidence_is_rejected() -> None:
    state = EvidenceLoopState(current_goal="produce file")
    record_tool_result(state, tool_name="Write", tool_input={"file_path": "out.txt"}, result={"content": "ok", "is_error": False}, turn_index=1)

    evaluation = evaluate_checkpoint(state, trigger="completion", final_text="Done", turn_index=2)

    assert evaluation.completion_confidence == "unsupported"
    assert evaluation.required_next_mode == "gather_completion_evidence"
    assert "not supported" in completion_redirect(evaluation)


def test_completion_with_clear_observed_support_is_allowed() -> None:
    state = EvidenceLoopState(current_goal="produce file")
    record_tool_result(state, tool_name="Write", tool_input={"file_path": "out.txt"}, result={"content": "ok", "is_error": False}, turn_index=1)
    record_tool_result(state, tool_name="Read", tool_input={"file_path": "out.txt"}, result={"content": "requested output", "is_error": False}, turn_index=2)

    result = evaluate_completion(state, final_text="Done")

    assert result["completion_confidence"] == "supported"
    assert result["supporting_evidence"] == ["requested output"]


def test_qualitative_non_executable_evidence_is_supported() -> None:
    state = EvidenceLoopState(current_goal="summarize document")
    record_tool_result(state, tool_name="Read", tool_input={"file_path": "notes.md"}, result={"content": "qualitative notes inspected", "is_error": False}, turn_index=1)

    result = evaluate_completion(state, final_text="Summary based on the inspected notes.")

    assert result["completion_confidence"] == "supported"


def test_initial_exploration_and_setup_are_not_prematurely_blocked() -> None:
    state = EvidenceLoopState(current_goal="understand task")
    record_tool_result(state, tool_name="Ls", tool_input={"path": "."}, result={"content": "README.md", "is_error": False}, turn_index=1)
    record_tool_result(state, tool_name="Read", tool_input={"file_path": "README.md"}, result={"content": "instructions", "is_error": False}, turn_index=2)

    assert maybe_build_intervention(state, turn_index=2) is None


class SyntheticEvidenceLoopClient:
    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[dict] = []

    def create_message(self, payload, stream):
        self.calls += 1
        self.payloads.append(payload)
        if self.calls == 1:
            return {"role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "artifact.txt", "content": "bad"}}]}
        if self.calls == 2:
            assert "without obtaining evidence" not in json.dumps(payload)
            return {"role": "assistant", "content": [{"type": "tool_use", "id": "w2", "name": "Write", "input": {"file_path": "artifact.txt", "content": "still bad"}}]}
        if self.calls == 3:
            assert "without obtaining evidence" in json.dumps(payload)
            return {"role": "assistant", "content": [{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "artifact.txt"}}]}
        if self.calls == 4:
            assert "still bad" in json.dumps(payload)
            return {"role": "assistant", "content": [{"type": "tool_use", "id": "w3", "name": "Write", "input": {"file_path": "artifact.txt", "content": "good"}}]}
        if self.calls == 5:
            return {"role": "assistant", "content": [{"type": "tool_use", "id": "r2", "name": "Read", "input": {"file_path": "artifact.txt"}}]}
        return {"role": "assistant", "content": [{"type": "text", "text": "Completed with observed artifact content."}]}


def test_synthetic_end_to_end_evidence_loop_and_telemetry(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    client = SyntheticEvidenceLoopClient()
    runner = Runner(
        client=client,
        repo=tmp_path,
        model="m",
        stream=False,
        debug_config=DebugConfig(mode=DebugMode.NORMAL, debug_root=debug_root),
    )

    result = runner.run("Create a generic artifact and ensure the result is observed.")

    assert result["transcript"]["evidence_loop"]["completion_confidence"] == "supported"
    assert any(item["kind"] == "observe_result" for item in result["transcript"]["evidence_loop"]["interventions"])
    run_dirs = list(debug_root.iterdir())
    assert run_dirs
    evidence_jsonl = run_dirs[0] / "evidence_loop.jsonl"
    state_json = run_dirs[0] / "evidence_loop_state.json"
    assert evidence_jsonl.exists()
    assert state_json.exists()
    assert "detected_material_actions" in state_json.read_text(encoding="utf-8")
