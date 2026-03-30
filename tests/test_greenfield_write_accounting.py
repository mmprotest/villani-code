from __future__ import annotations

from pathlib import Path

from villani_code.autonomous import VillaniModeController
from villani_code.mission import Mission, MissionExecutionState, MissionNode, MissionScratchpad, MissionType, NodePhase, NodeStatus
from villani_code.mission_bridge import (
    _extract_write_paths_from_text,
    _extract_blocked_write_paths_from_transcript,
    _extract_successful_write_paths_from_transcript,
    execute_mission_node_with_runner,
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


def test_explicit_denied_write_path_escapes_repository_is_blocked_not_observed() -> None:
    transcript = {
        "tool_invocations": [{"name": "write", "input": {"file_path": "/tmp/outside.txt"}}],
        "tool_results": [{"is_error": True, "content": "Path escapes repository"}],
    }
    observed = _extract_successful_write_paths_from_transcript({"transcript": transcript})
    blocked = _extract_blocked_write_paths_from_transcript({"transcript": transcript})
    assert observed == []
    assert blocked == ["/tmp/outside.txt"]


def test_streamed_text_only_writes_are_accounted_for_greenfield_scaffold() -> None:
    text = "\n".join(
        [
            r"write C:\sandbox\README.md",
            r"write C:\sandbox\game.py",
        ]
    )
    observed = _extract_write_paths_from_text(text)
    assert observed == [r"C:\sandbox\README.md", r"C:\sandbox\game.py"]

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
            "attempted_write_paths": [],
        },
        scratchpad=MissionScratchpad(mission_type="greenfield_build"),
    )
    assert outcome["status"] == "passed"
    assert outcome["user_deliverable_patch"] is True


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


def test_structured_transcript_write_accounting_still_works_as_before() -> None:
    transcript = {
        "tool_invocations": [{"name": "write", "input": {"file_path": "README.md"}}],
        "tool_results": [{"is_error": False, "content": "ok"}],
    }
    observed = _extract_successful_write_paths_from_transcript({"transcript": transcript})
    blocked = _extract_blocked_write_paths_from_transcript({"transcript": transcript})
    assert observed == ["README.md"]
    assert blocked == []


class _FallbackBlockedRunner:
    def run(self, _prompt: str, **_kwargs):
        return {
            "response": {"content": [{"type": "text", "text": "write /tmp/outside.txt"}]},
            "transcript": {
                "tool_invocations": [{"name": "write", "input": {"file_path": "/tmp/outside.txt"}}],
                "tool_results": [{"is_error": True, "content": "Path escapes repository"}],
            },
        }


def test_blocked_write_removes_same_path_from_fallback_observed_writes(tmp_path: Path) -> None:
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
    result = execute_mission_node_with_runner(_FallbackBlockedRunner(), mission, node, state)
    assert result.execution_payload["blocked_write_paths"] == ["/tmp/outside.txt"]
    assert result.execution_payload["observed_write_paths"] == []


class _WorkspaceWritingRunner:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def run(self, _prompt: str, **_kwargs):
        target = self.workspace_root / "src" / "game.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("print('game')\n", encoding="utf-8")
        return {
            "transcript": {
                "tool_invocations": [{"name": "write", "input": {"file_path": "src/game.py"}}],
                "tool_results": [{"is_error": False, "content": "Wrote src/game.py"}],
            }
        }


def test_greenfield_workspace_root_tracks_verified_files_outside_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    workspace_root = tmp_path / "sandbox"
    workspace_root.mkdir()
    mission = _greenfield_mission(repo_root)
    mission.mission_context["workspace_root"] = str(workspace_root)
    node = MissionNode(
        node_id="n1",
        title="Scaffold",
        phase=NodePhase.SCAFFOLD_PROJECT,
        objective="Create starter files",
        contract_type="scaffold_project",
        status=NodeStatus.READY,
    )
    state = MissionExecutionState(mission=mission, scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value))
    result = execute_mission_node_with_runner(_WorkspaceWritingRunner(workspace_root), mission, node, state)
    assert result.execution_payload["verified_successful_write_paths"] == ["src/game.py"]
    assert result.execution_payload["changed_files"] == ["src/game.py"]


class _HallucinatedScaffoldRunner:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def run(self, _prompt: str, **_kwargs):
        (self.workspace_root / "README.md").write_text("# demo\n", encoding="utf-8")
        (self.workspace_root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        return {
            "response": {
                "content": [
                    {
                        "type": "text",
                        "text": "Created README.md, pyproject.toml, src/game.py, tests/test_game.py",
                    }
                ]
            },
            "transcript": {
                "tool_invocations": [
                    {"name": "write", "input": {"file_path": "README.md"}},
                    {"name": "write", "input": {"file_path": "pyproject.toml"}},
                    {"name": "read", "input": {"file_path": "src/game.py"}},
                ],
                "tool_results": [
                    {"is_error": False, "content": "Wrote README.md"},
                    {"is_error": False, "content": "Wrote pyproject.toml"},
                    {"is_error": True, "content": "File not found: src/game.py"},
                ],
            },
        }


def test_scaffold_hallucination_does_not_pollute_verified_inventory(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    workspace_root = tmp_path / "sandbox"
    workspace_root.mkdir()
    mission = _greenfield_mission(repo_root)
    mission.mission_context["workspace_root"] = str(workspace_root)
    node = MissionNode(
        node_id="n1",
        title="Scaffold",
        phase=NodePhase.SCAFFOLD_PROJECT,
        objective="Create starter files",
        contract_type="scaffold_project",
        status=NodeStatus.READY,
    )
    state = MissionExecutionState(mission=mission, scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value))
    result = execute_mission_node_with_runner(_HallucinatedScaffoldRunner(workspace_root), mission, node, state)
    assert result.execution_payload["verified_files_present"] == ["README.md", "pyproject.toml"]
    assert "src/game.py" not in result.execution_payload["verified_files_present"]
