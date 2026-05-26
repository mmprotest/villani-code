from __future__ import annotations

from pathlib import Path

from villani_code.planning import ExecutionPlan, PlanRiskLevel, TaskMode
from villani_code.task_contract import (
    BehavioralCheck,
    ObservableKind,
    RequiredObservable,
    TaskOutcomeContract,
    build_task_outcome_contract,
    check_contract_satisfaction,
    format_contract_for_model,
)


class RuntimeCfg:
    def __init__(self, expected_files: list[str]) -> None:
        self.expected_files = expected_files


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_goal="goal",
        assumptions=[],
        relevant_files=["villani_code/state.py", "tests/test_state.py"],
        proposed_actions=[],
        risks=[],
        validation_steps=["pytest -q tests/test_state.py"],
        done_criteria=[],
        risk_level=PlanRiskLevel.LOW,
        non_trivial=False,
    )


def test_instruction_output_file_creates_required_observable(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Write output to reports/result.json",
        task_mode=TaskMode.GENERAL,
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )
    assert any(
        o.kind == ObservableKind.FILE.value and o.path == "reports/result.json"
        for o in contract.required_observables
    )


def test_execution_plan_relevant_files_become_preferred_targets(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Fix bug",
        task_mode="general",
        execution_plan=_plan(),
        benchmark_config=None,
        existing_preferred_targets=["README.md"],
    )
    assert "README.md" in contract.preferred_targets
    assert "villani_code/state.py" in contract.preferred_targets
    assert "tests/test_state.py" in contract.preferred_targets


def test_runtime_expected_files_become_required_observables(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Investigate",
        task_mode="general",
        execution_plan=None,
        benchmark_config=RuntimeCfg(["build/report.txt"]),
        existing_preferred_targets=[],
    )
    runtime_obs = [o for o in contract.required_observables if o.path == "build/report.txt"]
    assert runtime_obs
    assert runtime_obs[0].source == "runtime_config"


def test_contract_serialization_round_trip() -> None:
    original = TaskOutcomeContract(
        objective="obj",
        task_mode="general",
        success_predicate="ok",
        preferred_targets=["a.py"],
        confidence=0.7,
        source="manual",
    )
    loaded = TaskOutcomeContract.from_dict(original.to_dict())
    assert loaded == original


def test_empty_minimal_inputs_yield_valid_contract(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="",
        task_mode="",
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )
    assert isinstance(contract, TaskOutcomeContract)
    assert contract.objective == ""
    assert contract.task_mode == ""
    assert isinstance(contract.required_observables, list)
    assert isinstance(contract.behavioral_checks, list)


def test_formatted_contract_includes_objective_required_observables_and_behavioral_checks(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Fix failing test in src/foo.py",
        task_mode=TaskMode.GENERAL,
        execution_plan=_plan(),
        benchmark_config=RuntimeCfg(["build/report.txt"]),
        existing_preferred_targets=["src/foo.py"],
    )
    text = format_contract_for_model(contract)
    assert "<task_outcome_contract>" in text
    assert "objective: Fix failing test in src/foo.py" in text
    assert "required_observables:" in text
    assert "kind=file path=src/foo.py" in text
    assert "behavioral_checks:" in text
    assert "Run validation step: pytest -q tests/test_state.py" in text


def test_contract_checker_missing_required_file_is_unsatisfied(tmp_path: Path) -> None:
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(kind=ObservableKind.FILE.value, path="missing.txt", description="required file")
        ],
    )
    result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert result.satisfied is False


def test_contract_checker_existing_required_file_is_satisfied(tmp_path: Path) -> None:
    (tmp_path / "present.txt").write_text("ok", encoding="utf-8")
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(kind=ObservableKind.FILE.value, path="present.txt", description="required file")
        ],
    )
    result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert result.satisfied is True


def test_contract_checker_validation_artifact_observable_is_satisfied(tmp_path: Path) -> None:
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(
                kind=ObservableKind.VALIDATION_ARTIFACT.value,
                path="pytest -q tests/test_state.py",
                description="validation evidence",
            )
        ],
    )
    result = check_contract_satisfaction(
        tmp_path,
        contract,
        changed_files=[],
        validation_artifacts=["pytest -q tests/test_state.py (exit=0)"],
    )
    assert result.satisfied is True


def test_contract_checker_behavioral_command_is_satisfied_by_validation_evidence(tmp_path: Path) -> None:
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
        behavioral_checks=[BehavioralCheck(description="run tests", command="pytest -q", required=True)],
    )
    result = check_contract_satisfaction(
        tmp_path,
        contract,
        changed_files=[],
        validation_artifacts=["pytest -q (exit=0)"],
    )
    assert result.satisfied is True


def test_contract_checker_empty_contract_is_satisfied(tmp_path: Path) -> None:
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
    )
    result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert result.satisfied is True


def test_required_observable_round_trip_with_must_change_purpose() -> None:
    original = RequiredObservable(
        kind=ObservableKind.MODIFIED_FILE.value,
        path="villani_code/task_contract.py",
        description="must be updated",
        purpose="must_change",
    )
    loaded = RequiredObservable.from_dict(original.to_dict())
    assert loaded == original


def test_required_observable_round_trip_with_must_generate_purpose() -> None:
    original = RequiredObservable(
        kind=ObservableKind.GENERATED_FILE.value,
        path="build/report.json",
        description="generated artifact",
        purpose="must_generate",
    )
    loaded = RequiredObservable.from_dict(original.to_dict())
    assert loaded == original


def test_required_observable_from_legacy_payload_defaults_purpose_to_evidence() -> None:
    legacy = {
        "kind": ObservableKind.FILE.value,
        "path": "README.md",
        "description": "existing reference",
        "must_exist": True,
        "evidence_command": "",
        "source": "inferred",
    }
    loaded = RequiredObservable.from_dict(legacy)
    assert loaded.purpose == "evidence"


def test_observable_kind_includes_extended_values() -> None:
    kinds = {kind.value for kind in ObservableKind}
    assert ObservableKind.MODIFIED_FILE.value in kinds
    assert ObservableKind.GENERATED_FILE.value in kinds
    assert ObservableKind.GENERATED_DIRECTORY.value in kinds
    assert ObservableKind.VALIDATION_EVIDENCE.value in kinds
