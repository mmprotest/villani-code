from __future__ import annotations

from villani_code.mission import MissionScratchpad
from villani_code.mission_planner import MissionPlanner
from villani_code.verification.outcomes import classify_node_outcome


def test_greenfield_classification_creation_prompt_defaults_safe() -> None:
    planner = MissionPlanner()
    mission_type = planner.classify_mission_type(
        "build me a fun python game",
        repo_signals={
            "workspace_empty_or_internal_only": True,
            "workspace_lightweight_hints_only": False,
            "existing_project_detected": False,
        },
    )
    assert mission_type.value == "greenfield_build"


def test_greenfield_classification_respects_bugfix_language() -> None:
    planner = MissionPlanner()
    mission_type = planner.classify_mission_type(
        "build me an app to fix this regression",
        repo_signals={"workspace_empty_or_internal_only": True, "existing_project_detected": False},
    )
    assert mission_type.value != "greenfield_build"


def test_greenfield_read_only_phase_write_is_contract_violation() -> None:
    outcome = classify_node_outcome(
        contract_type="inspect",
        static_result={"findings": []},
        command_results=[],
        changed_files=["README.md"],
        mission_type="greenfield_build",
        node_phase="inspect_workspace",
        execution_payload={},
        scratchpad=MissionScratchpad(mission_type="greenfield_build"),
    )
    assert outcome["status"] == "failed"
    assert "contract violation" in outcome["reason"]


def test_validation_claim_without_command_evidence_is_unproven() -> None:
    outcome = classify_node_outcome(
        contract_type="validate_project",
        static_result={"findings": []},
        command_results=[],
        changed_files=[],
        mission_type="greenfield_build",
        node_phase="validate_project",
        execution_payload={
            "self_reported_validation_claim": True,
            "self_reported_validation_without_evidence": True,
        },
        scratchpad=MissionScratchpad(mission_type="greenfield_build"),
    )
    assert outcome["status"] == "failed"
    assert "without command evidence" in outcome["reason"]
    assert outcome["self_reported_validation_without_evidence"] is True
