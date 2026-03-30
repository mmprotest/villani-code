from __future__ import annotations

from pathlib import Path

from villani_code.autonomous import VillaniModeController
from villani_code.mission import (
    Mission,
    MissionExecutionState,
    MissionNode,
    MissionObjective,
    MissionScratchpad,
    MissionType,
    NodePhase,
    NodeStatus,
    NormalizedNodeOutcome,
)
from villani_code.recovery import RecoveryDecision
from villani_code.verification.mission import evaluate_mission_status


class _Runner:
    def run(self, _prompt: str, execution_budget=None):
        return {
            "response": {"content": [{"type": "text", "text": "done"}]},
            "transcript": {"tool_results": []},
            "execution": {"terminated_reason": "completed"},
        }


def _state(tmp_path: Path) -> tuple[VillaniModeController, MissionExecutionState, MissionNode]:
    failed = MissionNode(
        node_id="n-validate",
        title="Validate",
        phase=NodePhase.VALIDATE_PROJECT,
        objective="validate",
        contract_type="validate_project",
        status=NodeStatus.FAILED,
        validation_commands=["python app.py"],
    )
    mission = Mission(
        mission_id="m1",
        user_goal="build game",
        mission_type=MissionType.GREENFIELD_BUILD,
        success_criteria=[],
        repo_root=str(tmp_path),
        nodes=[failed],
        objective=MissionObjective(direction="snake_cli_game"),
    )
    state = MissionExecutionState(
        mission=mission,
        scratchpad=MissionScratchpad(
            mission_type=MissionType.GREENFIELD_BUILD.value,
            chosen_project_direction="snake_cli_game",
        ),
    )
    controller = VillaniModeController(_Runner(), tmp_path, steering_objective="build something")
    return controller, state, failed


def test_validation_failure_creates_and_selects_runnable_recovery_node(tmp_path: Path) -> None:
    controller, state, failed = _state(tmp_path)

    controller._handle_recovery(
        state,
        failed,
        {
            "status": "failed",
            "delta_classification": "no_improvement",
            "patch_no_improvement": True,
            "validation_summary": {
                "failed_commands": [{"command": "python app.py  # UnicodeEncodeError cp1252 emoji"}],
            },
            "user_space_changed_files": ["app.py"],
        },
    )

    recovery_nodes = [n for n in state.mission.nodes if n.created_from_node_id == failed.node_id]
    assert recovery_nodes
    assert recovery_nodes[0].status == NodeStatus.READY
    assert "encoding-safe" in recovery_nodes[0].objective
    selected = controller._select_next_node(state)
    assert selected is not None
    assert selected.node_id == recovery_nodes[0].node_id


def test_no_progress_counter_resets_after_recovery_insertion(tmp_path: Path) -> None:
    controller, state, failed = _state(tmp_path)
    state.consecutive_no_progress = 3

    controller._handle_recovery(state, failed, {"status": "failed", "delta_classification": "no_improvement"})

    assert state.consecutive_no_progress == 0
    assert state.recovery_nodes_inserted_last >= 1


def test_dynamic_recovery_insertion_is_visible_without_restart(tmp_path: Path) -> None:
    controller, state, failed = _state(tmp_path)

    assert controller._select_next_node(state) is None
    controller._handle_recovery(state, failed, {"status": "failed", "delta_classification": "no_improvement"})

    assert controller._select_next_node(state) is not None


def test_recovery_creation_failure_reports_specific_reason(tmp_path: Path) -> None:
    controller, state, failed = _state(tmp_path)
    state.consecutive_no_progress = 3
    state.normalized_node_outcomes.append(
        NormalizedNodeOutcome(
            node_id=failed.node_id,
            node_phase=failed.phase.value,
            contract_status="contract_failed",
            mission_progress_status="no_progress",
        )
    )

    controller.recovery.plan_recovery = lambda *_args, **_kwargs: RecoveryDecision(  # type: ignore[method-assign]
        strategy="rescope",
        reason="planner returned nothing",
        nodes=[],
    )
    controller._handle_recovery(state, failed, {"status": "failed"})

    outcome, reason = evaluate_mission_status(state)
    assert outcome is not None
    assert "produced no runnable nodes" in reason


def test_narrative_success_text_does_not_override_failed_validation_truth(tmp_path: Path) -> None:
    _controller, state, _failed = _state(tmp_path)
    state.normalized_node_outcomes.append(
        NormalizedNodeOutcome(
            node_id="n1",
            node_phase=NodePhase.VALIDATE_PROJECT.value,
            contract_status="contract_clean_success",
            mission_progress_status="validated_success",
            deliverable_paths=["app.py"],
            validation_truth_status="failed",
            command_results=[{"command": "pytest -q", "exit": 1, "summary": "31/31 PASSED narrative"}],
        )
    )

    outcome, reason = evaluate_mission_status(state)
    assert outcome is not None
    assert outcome.value == "partial_success_built_validation_failed"
    assert "command validation failed" in reason.lower()
