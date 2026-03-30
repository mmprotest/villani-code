from __future__ import annotations

from pathlib import Path

from villani_code.autonomous import VillaniModeController
from villani_code.mission_bridge import execute_mission_node_with_runner
from villani_code.mission import (
    Mission,
    MissionExecutionState,
    MissionNode,
    MissionScratchpad,
    MissionType,
    NodePhase,
    NodeStatus,
    NormalizedNodeOutcome,
    reduce_normalized_mission_progress,
)
from villani_code.repo_signal_planner import collect_repo_signals
from villani_code.verification.mission import evaluate_mission_status
from villani_code.verification.outcomes import classify_node_outcome


class _NoopRunner:
    def run(self, _prompt: str, **_kwargs):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


class _ThinRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, _prompt: str, **_kwargs):
        self.calls += 1
        return {"response": {"content": [{"type": "text", "text": "<task>inspect_workspace</task><objective>Understand workspace structure...</objective>"}]}}


class _EmptyReplyRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, _prompt: str, **_kwargs):
        self.calls += 1
        return {"response": {"content": []}}


class _FlowRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def run(self, _prompt: str, **_kwargs):
        policy = getattr(self, "_villani_phase_tool_policy", {}) or {}
        phase = str(policy.get("node_phase", ""))
        if phase == "define_objective":
            return {"response": {"content": [{"type": "text", "text": "<task>define_objective</task>"}]}}
        if phase == "scaffold_project":
            (self.repo / "README.md").write_text("# fun python game\n", encoding="utf-8")
            (self.repo / "game.py").write_text("def main():\n    print('welcome')\n\nif __name__ == '__main__':\n    main()\n", encoding="utf-8")
            return {"execution": {"changed_files": ["README.md", "game.py"], "meaningful_patch": True}}
        if phase == "implement_increment":
            (self.repo / "game.py").write_text(
                "def main():\n    secret = 'cat'\n    print('guess the word')\n    guess = 'cat'\n    print('you win' if guess == secret else 'try again')\n\nif __name__ == '__main__':\n    main()\n",
                encoding="utf-8",
            )
            return {"execution": {"changed_files": ["game.py"], "meaningful_patch": True}}
        if phase == "validate_project":
            return {
                "execution": {
                    "command_results": [{"command": "python -m py_compile game.py", "exit": 0, "stdout": "", "stderr": ""}]
                }
            }
        return {"response": {"content": [{"type": "text", "text": "summary"}]}}


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


def test_repo_signals_mark_readme_only_workspace_as_sparse_greenfield(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# notes\n", encoding="utf-8")
    signals = collect_repo_signals(str(tmp_path))
    assert signals["workspace_lightweight_hints_only"] is True
    assert signals["workspace_sparse_greenfield_like"] is True


def test_repo_signals_mark_partial_non_runnable_skeleton_as_sparse_greenfield(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "engine.py").write_text("def tick():\n    return 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# game skeleton\n", encoding="utf-8")
    signals = collect_repo_signals(str(tmp_path))
    assert signals["existing_project_detected"] is True
    assert signals["workspace_sparse_greenfield_like"] is True
    assert signals["entrypoint_like_files"] == []


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


def test_read_only_state_progress_is_not_reduced_to_stagnated(tmp_path: Path) -> None:
    state = MissionExecutionState(
        mission=_mission(tmp_path),
        scratchpad=MissionScratchpad(
            mission_type=MissionType.GREENFIELD_BUILD.value,
            chosen_project_direction="snake_cli_game",
            next_required_action="scaffold_project",
        ),
    )
    state.normalized_node_outcomes.append(
        NormalizedNodeOutcome(
            node_id="n1",
            node_phase="inspect_workspace",
            contract_status="contract_clean_success",
            mission_progress_status="state_progress",
            next_recommended_action="define_objective",
        )
    )
    state.normalized_node_outcomes.append(
        NormalizedNodeOutcome(
            node_id="n2",
            node_phase="define_objective",
            contract_status="contract_clean_success",
            mission_progress_status="state_progress",
            next_recommended_action="scaffold_project",
        )
    )
    reduced = reduce_normalized_mission_progress(state)
    assert reduced.terminal_state == "in_progress"
    assert reduced.next_recommended_action == "scaffold_project"


def test_greenfield_empty_sandbox_inspect_passes_with_thin_reply(tmp_path: Path) -> None:
    mission = _mission(tmp_path)
    mission.mission_context["repo_signals"] = {
        "workspace_empty_or_internal_only": True,
        "existing_project_detected": False,
        "language_hints": ["python"],
    }
    node = MissionNode(
        node_id="n1",
        title="Inspect workspace",
        phase=NodePhase.INSPECT_WORKSPACE,
        objective="inspect",
        contract_type="inspect_workspace",
        status=NodeStatus.READY,
    )
    state = MissionExecutionState(mission=mission, scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value))
    runner = _ThinRunner()
    result = execute_mission_node_with_runner(runner, mission, node, state)
    outcome = classify_node_outcome(
        contract_type=node.contract_type,
        static_result={"findings": list(result.execution_payload.get("controller_findings", []))},
        command_results=result.commands,
        changed_files=result.changed_files,
        prose_only=result.prose_only,
        mission_type="greenfield_build",
        node_phase="inspect_workspace",
        execution_payload=result.execution_payload,
        scratchpad=state.scratchpad,
    )
    state.normalized_node_outcomes.append(
        NormalizedNodeOutcome(
            node_id="n1",
            node_phase="inspect_workspace",
            contract_status=outcome["phase_contract_status"],
            mission_progress_status=outcome["mission_progress_status"],
            next_recommended_action="define_objective",
        )
    )
    reduced = reduce_normalized_mission_progress(state)
    mission_outcome, _reason = evaluate_mission_status(state)
    assert runner.calls == 0
    assert outcome["status"] == "passed"
    assert outcome["mission_progress_status"] == "state_progress"
    assert reduced.terminal_state == "in_progress"
    assert mission_outcome is None


def test_greenfield_sparse_readme_inspect_uses_controller_native_path(tmp_path: Path) -> None:
    mission = _mission(tmp_path)
    mission.mission_context["repo_signals"] = {
        "workspace_empty_or_internal_only": False,
        "workspace_lightweight_hints_only": True,
        "workspace_sparse_greenfield_like": True,
        "existing_project_detected": False,
        "language_hints": ["python"],
    }
    node = MissionNode(
        node_id="n1",
        title="Inspect workspace",
        phase=NodePhase.INSPECT_WORKSPACE,
        objective="inspect",
        contract_type="inspect_workspace",
        status=NodeStatus.READY,
    )
    state = MissionExecutionState(mission=mission, scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value))
    runner = _EmptyReplyRunner()
    result = execute_mission_node_with_runner(runner, mission, node, state)
    outcome = classify_node_outcome(
        contract_type=node.contract_type,
        static_result={"findings": list(result.execution_payload.get("controller_findings", []))},
        command_results=result.commands,
        changed_files=result.changed_files,
        prose_only=result.prose_only,
        mission_type="greenfield_build",
        node_phase="inspect_workspace",
        execution_payload=result.execution_payload,
        scratchpad=state.scratchpad,
    )
    assert runner.calls == 0
    assert outcome["status"] == "passed"
    assert "sparse/partial scaffold" in " ".join(result.execution_payload.get("controller_findings", []))


def test_greenfield_sparse_partial_skeleton_inspect_still_passes(tmp_path: Path) -> None:
    mission = _mission(tmp_path)
    mission.mission_context["repo_signals"] = {
        "workspace_empty_or_internal_only": False,
        "workspace_lightweight_hints_only": False,
        "workspace_sparse_greenfield_like": True,
        "existing_project_detected": True,
        "language_hints": ["python"],
        "entrypoint_like_files": [],
    }
    node = MissionNode(
        node_id="n1",
        title="Inspect workspace",
        phase=NodePhase.INSPECT_WORKSPACE,
        objective="inspect",
        contract_type="inspect_workspace",
        status=NodeStatus.READY,
    )
    state = MissionExecutionState(mission=mission, scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value))
    runner = _ThinRunner()
    result = execute_mission_node_with_runner(runner, mission, node, state)
    assert runner.calls == 0
    assert result.execution_payload.get("controller_native") is True


def test_greenfield_define_objective_thin_reply_uses_controller_state(tmp_path: Path) -> None:
    mission = _mission(tmp_path)
    mission.mission_context["repo_signals"] = {"workspace_empty_or_internal_only": True, "language_hints": ["python"]}
    mission.objective.direction = "snake_cli_game"
    mission.objective.deliverable_kind = ["game"]
    mission.objective.initial_validation_strategy = ["python -m py_compile game.py"]
    node = MissionNode(
        node_id="n2",
        title="Define objective",
        phase=NodePhase.DEFINE_OBJECTIVE,
        objective="define",
        contract_type="define_objective",
        status=NodeStatus.READY,
    )
    state = MissionExecutionState(
        mission=mission,
        scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value, chosen_project_direction="snake_cli_game"),
    )
    result = execute_mission_node_with_runner(_ThinRunner(), mission, node, state)
    outcome = classify_node_outcome(
        contract_type=node.contract_type,
        static_result={"findings": []},
        command_results=result.commands,
        changed_files=result.changed_files,
        prose_only=True,
        mission_type="greenfield_build",
        node_phase="define_objective",
        execution_payload=result.execution_payload,
        scratchpad=MissionScratchpad(mission_type="greenfield_build", chosen_project_direction="snake_cli_game", next_required_action="scaffold_project"),
        mission_objective={
            "repo_state_type": "empty_sandbox",
            "task_shape": "greenfield_build",
            "deliverable_kind": ["game"],
            "direction": "snake_cli_game",
            "initial_validation_strategy": ["python -m py_compile game.py"],
        },
    )
    assert outcome["status"] == "passed"


def test_greenfield_define_objective_empty_reply_uses_controller_state(tmp_path: Path) -> None:
    mission = _mission(tmp_path)
    mission.mission_context["repo_signals"] = {
        "workspace_sparse_greenfield_like": True,
        "workspace_empty_or_internal_only": False,
        "language_hints": ["python"],
    }
    mission.objective.direction = "snake_cli_game"
    mission.objective.repo_state_type = "sparse_scaffold"
    mission.objective.task_shape = "greenfield_build"
    mission.objective.deliverable_kind = ["game"]
    mission.objective.initial_validation_strategy = ["python -m py_compile game.py"]
    node = MissionNode(
        node_id="n2",
        title="Define objective",
        phase=NodePhase.DEFINE_OBJECTIVE,
        objective="define",
        contract_type="define_objective",
        status=NodeStatus.READY,
    )
    state = MissionExecutionState(
        mission=mission,
        scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value, chosen_project_direction="snake_cli_game"),
    )
    runner = _EmptyReplyRunner()
    result = execute_mission_node_with_runner(runner, mission, node, state)
    outcome = classify_node_outcome(
        contract_type=node.contract_type,
        static_result={"findings": []},
        command_results=result.commands,
        changed_files=result.changed_files,
        prose_only=True,
        mission_type="greenfield_build",
        node_phase="define_objective",
        execution_payload=result.execution_payload,
        scratchpad=state.scratchpad,
        mission_objective={
            "repo_state_type": mission.objective.repo_state_type,
            "task_shape": mission.objective.task_shape,
            "deliverable_kind": mission.objective.deliverable_kind,
            "direction": mission.objective.direction,
            "initial_validation_strategy": mission.objective.initial_validation_strategy,
        },
    )
    assert runner.calls == 0
    assert outcome["status"] == "passed"


def test_ready_greenfield_recovery_node_keeps_mission_in_progress(tmp_path: Path) -> None:
    mission = _mission(tmp_path)
    mission.nodes.append(
        MissionNode(
            node_id="recover-1",
            title="Force user-space scaffold",
            phase=NodePhase.SCAFFOLD_PROJECT,
            objective="recover",
            contract_type="scaffold_project",
            status=NodeStatus.READY,
            created_from_node_id="failed-node",
        )
    )
    state = MissionExecutionState(
        mission=mission,
        scratchpad=MissionScratchpad(mission_type=MissionType.GREENFIELD_BUILD.value, next_required_action="scaffold_project"),
        consecutive_no_progress=5,
    )
    state.normalized_node_outcomes.append(
        NormalizedNodeOutcome(
            node_id="n1",
            node_phase="define_objective",
            contract_status="contract_clean_success",
            mission_progress_status="state_progress",
            next_recommended_action="scaffold_project",
        )
    )
    reduced = reduce_normalized_mission_progress(state)
    outcome, _reason = evaluate_mission_status(state)
    assert reduced.terminal_state == "in_progress"
    assert outcome is None


def test_mission_stays_in_progress_with_known_next_action_despite_no_progress_counters(tmp_path: Path) -> None:
    state = MissionExecutionState(
        mission=_mission(tmp_path),
        scratchpad=MissionScratchpad(
            mission_type=MissionType.GREENFIELD_BUILD.value,
            chosen_project_direction="snake_cli_game",
            next_required_action="scaffold_project",
        ),
        consecutive_no_progress=8,
        repeated_delta_states=4,
        consecutive_no_model_activity=6,
    )
    state.normalized_node_outcomes.append(
        NormalizedNodeOutcome(
            node_id="n1",
            node_phase="inspect_workspace",
            contract_status="contract_clean_success",
            mission_progress_status="state_progress",
            next_recommended_action="scaffold_project",
        )
    )
    reduced = reduce_normalized_mission_progress(state)
    mission_outcome, _reason = evaluate_mission_status(state)
    assert reduced.terminal_state == "in_progress"
    assert mission_outcome is None


def test_prompt_empty_sandbox_fun_python_game_expected_greenfield_flow(tmp_path: Path) -> None:
    runner = _FlowRunner(tmp_path)
    controller = VillaniModeController(runner, tmp_path, steering_objective="Here is an empty sandbox. I want you to build me a fun python game. Go.")
    run_payload = controller.run()
    report = dict(run_payload.get("report", {}) or {})
    phases = [str(item.get("node_phase", "")) for item in report.get("validation_results", [])]
    assert phases[:6] == [
        "inspect_workspace",
        "define_objective",
        "scaffold_project",
        "implement_increment",
        "validate_project",
        "summarize_outcome",
    ]
    assert (tmp_path / "game.py").exists()
