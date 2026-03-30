from __future__ import annotations

from pathlib import Path

from villani_code.autonomous import VillaniModeController
from villani_code.mission import Mission, MissionExecutionState, MissionNode, MissionScratchpad, MissionType, NodePhase, NodeStatus
from villani_code.mission_bridge import (
    _extract_blocked_write_paths_from_transcript,
    _extract_successful_write_paths_from_transcript,
)
from villani_code.verification.outcomes import classify_node_outcome


class _NoopRunner:
    def run(self, _prompt: str, **_kwargs):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


def _greenfield_mission(tmp_path: Path) -> Mission:
    return Mission(
        mission_id="m-write-accounting",
        user_goal="build me a small game",
        mission_type=MissionType.GREENFIELD_BUILD,
        success_criteria=[],
        repo_root=str(tmp_path),
        nodes=[],
    )


def test_external_successful_writes_are_observed_not_blocked_and_count_for_scaffold() -> None:
    transcript = {
        "tool_invocations": [
            {"name": "write", "input": {"file_path": r"C:\Users\Simon\OneDrive\Documents\Python Scripts\villani_sandbox\README.md"}},
            {"name": "write", "input": {"file_path": r"C:\Users\Simon\OneDrive\Documents\Python Scripts\villani_sandbox\game.py"}},
        ],
        "tool_results": [
            {"is_error": False, "content": "ok"},
            {"is_error": False, "content": "ok"},
        ],
    }
    observed = _extract_successful_write_paths_from_transcript({"transcript": transcript})
    blocked = _extract_blocked_write_paths_from_transcript({"transcript": transcript})
    assert len(observed) == 2
    assert blocked == []

    outcome = classify_node_outcome(
        contract_type="scaffold_project",
        static_result={"findings": []},
        command_results=[],
        changed_files=[],
        mission_type="greenfield_build",
        node_phase="scaffold_project",
        execution_payload={
            "observed_write_paths": observed,
            "blocked_write_paths": [],
            "attempted_write_paths": observed,
        },
        scratchpad=MissionScratchpad(mission_type="greenfield_build"),
    )
    assert outcome["status"] == "passed"
    assert outcome["user_deliverable_patch"] is True
    assert outcome["blocked_write_paths"] == []


def test_explicit_denied_write_is_blocked_not_observed() -> None:
    transcript = {
        "tool_invocations": [{"name": "write", "input": {"file_path": "/tmp/outside.txt"}}],
        "tool_results": [{"is_error": True, "content": "Write forbidden: path outside allowed scope"}],
    }
    observed = _extract_successful_write_paths_from_transcript({"transcript": transcript})
    blocked = _extract_blocked_write_paths_from_transcript({"transcript": transcript})
    assert observed == []
    assert blocked == ["/tmp/outside.txt"]


def test_repo_local_changed_files_behavior_is_preserved() -> None:
    outcome = classify_node_outcome(
        contract_type="scaffold_project",
        static_result={"findings": []},
        command_results=[],
        changed_files=["src/app.py"],
        mission_type="greenfield_build",
        node_phase="scaffold_project",
        execution_payload={},
        scratchpad=MissionScratchpad(mission_type="greenfield_build"),
    )
    assert outcome["status"] == "passed"
    assert outcome["user_space_changed_files"] == ["src/app.py"]


def test_internal_only_observed_writes_do_not_count_as_greenfield_deliverables() -> None:
    outcome = classify_node_outcome(
        contract_type="implement_increment",
        static_result={"findings": []},
        command_results=[],
        changed_files=[],
        mission_type="greenfield_build",
        node_phase="implement_increment",
        execution_payload={
            "observed_write_paths": [".villani/logs/trace.json", ".villani_code/state.json"],
            "attempted_write_paths": [".villani/logs/trace.json", ".villani_code/state.json"],
            "blocked_write_paths": [],
        },
        scratchpad=MissionScratchpad(mission_type="greenfield_build"),
    )
    assert outcome["status"] == "failed"
    assert outcome["user_deliverable_patch"] is False


def test_greenfield_progress_records_observed_user_deliverables_when_changed_files_empty(tmp_path: Path) -> None:
    controller = VillaniModeController(_NoopRunner(), tmp_path)
    mission = _greenfield_mission(tmp_path)
    node = MissionNode(
        node_id="n1",
        title="Scaffold",
        phase=NodePhase.SCAFFOLD_PROJECT,
        objective="Create starter files",
        contract_type="scaffold_project",
        status=NodeStatus.READY,
    )
    state = MissionExecutionState(mission=mission, scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value))
    persisted = controller._record_greenfield_progress(
        state,
        node,
        changed_files=[],
        observed_write_paths=[r"C:\sandbox\README.md", r"C:\sandbox\game.py"],
        execution_payload={},
        node_status="passed",
    )
    assert persisted == [r"C:\sandbox\README.md", r"C:\sandbox\game.py"]
    assert state.greenfield_progress["successful_greenfield_scaffold"] is True


def test_read_only_phase_blocked_write_remains_recoverable_contract_violation() -> None:
    outcome = classify_node_outcome(
        contract_type="inspect",
        static_result={"findings": ["workspace metadata captured"]},
        command_results=[],
        changed_files=[],
        mission_type="greenfield_build",
        node_phase="inspect_workspace",
        execution_payload={
            "attempted_write_paths": ["README.md"],
            "blocked_write_paths": ["README.md"],
            "rejected_actions": [{"action_type": "write_file"}],
        },
        scratchpad=MissionScratchpad(mission_type="greenfield_build"),
    )
    assert outcome["user_deliverable_patch"] is False
    assert outcome["phase_contract_status"] == "contract_violation_recovered"
