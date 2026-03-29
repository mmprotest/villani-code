from __future__ import annotations

from pathlib import Path

from villani_code.autonomous import VillaniModeController
from villani_code.mission import Mission, MissionExecutionState, MissionScratchpad, MissionType
from villani_code.verification.mission import evaluate_mission_status
from villani_code.verification.outcomes import classify_node_outcome


class _NoopRunner:
    def run(self, _prompt: str, **_kwargs):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


def _mission(tmp_path: Path) -> Mission:
    return Mission(
        mission_id="m1",
        user_goal="build me something useful",
        mission_type=MissionType.GREENFIELD_BUILD,
        success_criteria=[],
        repo_root=str(tmp_path),
        nodes=[],
    )


def test_greenfield_next_action_prefers_validation_after_runnable_entrypoint() -> None:
    scratchpad = MissionScratchpad(
        mission_type=MissionType.GREENFIELD_BUILD.value,
        chosen_project_direction="python_cli_utility",
        has_user_space_scaffolding=True,
        has_runnable_entrypoint=True,
        validation_proven=False,
    )
    assert scratchpad.derive_next_action() == "validate_project"


def test_greenfield_validate_without_commands_stays_unproven() -> None:
    outcome = classify_node_outcome(
        contract_type="validate_project",
        static_result={"findings": []},
        command_results=[],
        changed_files=[],
        mission_type="greenfield_build",
        node_phase="validate_project",
        execution_payload={},
        scratchpad=MissionScratchpad(mission_type="greenfield_build"),
    )
    assert outcome["verification_status"] == "validation_unproven"
    assert outcome["status"] == "failed"


def test_direction_reconciles_to_realized_artifact(tmp_path: Path) -> None:
    controller = VillaniModeController(_NoopRunner(), tmp_path)
    state = MissionExecutionState(
        mission=_mission(tmp_path),
        scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value, chosen_project_direction="python_cli_utility"),
    )
    state.greenfield_selection = {"project_type": "python_cli_utility"}
    controller._sync_greenfield_direction_from_artifacts(state, ["wordguess.py"])
    assert state.scratchpad.chosen_project_direction == "word_guessing_game_cli"
    assert state.greenfield_selection["project_type"] == "word_guessing_game_cli"


def test_completion_gate_requires_validate_project_command_evidence(tmp_path: Path) -> None:
    state = MissionExecutionState(
        mission=_mission(tmp_path),
        scratchpad=MissionScratchpad(
            mission_type=MissionType.GREENFIELD_BUILD.value,
            has_runnable_entrypoint=True,
            validation_proven=True,
        ),
    )
    state.greenfield_progress = {"deliverable_paths": ["app.py"]}
    state.verification_history = [
        {"node_phase": "implement_vertical_slice", "validation_evidence_kind": "real_command_results"}
    ]
    outcome, _reason = evaluate_mission_status(state)
    assert outcome is None
