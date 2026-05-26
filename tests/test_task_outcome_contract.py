from __future__ import annotations

from pathlib import Path

from villani_code.planning import ExecutionPlan, PlanRiskLevel, TaskMode
from villani_code.task_contract import ObservableKind, TaskOutcomeContract, build_task_outcome_contract


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
