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
    extract_instruction_paths,
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
        o.kind == ObservableKind.GENERATED_FILE.value and o.path == "reports/result.json"
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
    assert "kind=modified_file path=src/foo.py" in text
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


def test_contract_checker_existing_file_with_must_change_without_diff_is_unsatisfied(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("print('ok')\n", encoding="utf-8")
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(
                kind=ObservableKind.EXISTING_FILE.value,
                path="src/foo.py",
                description="must change existing file",
                purpose="must_change",
            )
        ],
    )
    result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert result.satisfied is False
    assert any(f.category == "missing_change_evidence" for f in result.findings)


def test_contract_checker_existing_file_with_must_change_with_diff_is_satisfied(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("print('ok')\n", encoding="utf-8")
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(
                kind=ObservableKind.EXISTING_FILE.value,
                path="src/foo.py",
                description="must change existing file",
                purpose="must_change",
            )
        ],
    )
    result = check_contract_satisfaction(
        tmp_path, contract, changed_files=["src/foo.py"], validation_artifacts=[]
    )
    assert result.satisfied is True


def test_contract_checker_generated_file_observable_satisfied_when_file_exists(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "report.txt").write_text("ok\n", encoding="utf-8")
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(
                kind=ObservableKind.GENERATED_FILE.value,
                path="build/report.txt",
                description="generated output",
                purpose="must_generate",
            )
        ],
    )
    result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert result.satisfied is True


def test_contract_checker_reference_file_observable_satisfied_when_file_exists_unchanged(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    contract = TaskOutcomeContract(
        objective="o",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(
                kind=ObservableKind.EXISTING_FILE.value,
                path="docs/guide.md",
                description="reference file",
                purpose="reference",
            )
        ],
    )
    result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert result.satisfied is True


def test_extract_instruction_paths_included_patterns() -> None:
    assert extract_instruction_paths("Fix src/foo.py") == ["src/foo.py"]
    assert extract_instruction_paths("Write to output/results.txt") == ["output/results.txt"]
    assert extract_instruction_paths("Create `report.jsonl`") == ["report.jsonl"]
    assert extract_instruction_paths("Inspect README.md") == ["README.md"]


def test_extract_instruction_paths_excluded_non_paths() -> None:
    assert extract_instruction_paths("Use Python 3.11") == []
    assert extract_instruction_paths("Install package foo.bar") == []
    assert extract_instruction_paths("The function my.module.name failed") == []
    assert extract_instruction_paths("Version v1.2.3 is broken") == []
    assert extract_instruction_paths("Use sklearn.model_selection") == []




def test_classify_fix_instruction_marks_modified_file(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Fix the bug in src/foo.py",
        task_mode=TaskMode.GENERAL,
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )
    obs = next(o for o in contract.required_observables if o.path == "src/foo.py")
    assert obs.kind == ObservableKind.MODIFIED_FILE.value
    assert obs.purpose == "must_change"


def test_classify_write_instruction_marks_generated_file(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Write the result to output/results.txt",
        task_mode=TaskMode.GENERAL,
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )
    obs = next(o for o in contract.required_observables if o.path == "output/results.txt")
    assert obs.kind == ObservableKind.GENERATED_FILE.value
    assert obs.purpose == "must_generate"


def test_classify_inspect_instruction_marks_existing_reference(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Inspect config/settings.yaml",
        task_mode=TaskMode.GENERAL,
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )
    obs = next(o for o in contract.required_observables if o.path == "config/settings.yaml")
    assert obs.kind == ObservableKind.EXISTING_FILE.value
    assert obs.purpose == "reference"


def test_classify_use_instruction_marks_existing_reference(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Use README.md as context",
        task_mode=TaskMode.GENERAL,
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )
    obs = next(o for o in contract.required_observables if o.path == "README.md")
    assert obs.kind == ObservableKind.EXISTING_FILE.value
    assert obs.purpose == "reference"


def test_classify_multiple_path_mentions_returns_all(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Fix src/foo.py and write output to build/results.json. Also inspect config/settings.yaml",
        task_mode=TaskMode.GENERAL,
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )
    paths = {o.path for o in contract.required_observables}
    assert {"src/foo.py", "build/results.json", "config/settings.yaml"}.issubset(paths)

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


def test_repair_contract_requires_both_change_and_validation_evidence(tmp_path: Path) -> None:
    contract = TaskOutcomeContract(
        objective="repair",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(
                kind=ObservableKind.MODIFIED_FILE.value,
                path="src/foo.py",
                description="must change",
                purpose="must_change",
            ),
            RequiredObservable(
                kind=ObservableKind.VALIDATION_EVIDENCE.value,
                path="",
                description="must validate",
                purpose="must_validate",
                must_exist=False,
            ),
        ],
    )

    only_changed = check_contract_satisfaction(
        tmp_path, contract, changed_files=["src/foo.py"], validation_artifacts=[]
    )
    assert only_changed.satisfied is False

    only_validated = check_contract_satisfaction(
        tmp_path,
        contract,
        changed_files=[],
        validation_artifacts=["pytest -q tests/test_state.py (exit=0)"],
    )
    assert only_validated.satisfied is False

    both = check_contract_satisfaction(
        tmp_path,
        contract,
        changed_files=["src/foo.py"],
        validation_artifacts=["pytest -q tests/test_state.py (exit=0)"],
    )
    assert both.satisfied is True


def test_generated_output_contract_only_requires_generated_artifact_without_behavioral_checks(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "report.txt").write_text("ok\n", encoding="utf-8")
    contract = TaskOutcomeContract(
        objective="generate",
        task_mode="general",
        success_predicate="s",
        required_observables=[
            RequiredObservable(
                kind=ObservableKind.GENERATED_FILE.value,
                path="build/report.txt",
                description="generated output",
                purpose="must_generate",
            )
        ],
        behavioral_checks=[],
    )

    result = check_contract_satisfaction(
        tmp_path, contract, changed_files=[], validation_artifacts=[]
    )
    assert result.satisfied is True


def test_build_repair_contract_adds_validation_requirements(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Fix bug in src/foo.py",
        task_mode=TaskMode.GENERAL,
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )

    assert any(
        c.description == "Validate the modified behavior or explain the validation evidence"
        and c.required is True
        for c in contract.behavioral_checks
    )
    assert any(
        o.kind == ObservableKind.VALIDATION_EVIDENCE.value
        and o.purpose == "must_validate"
        and o.path == ""
        for o in contract.required_observables
    )


def test_extract_instruction_paths_preserves_absolute_nginx_path() -> None:
    paths = extract_instruction_paths("Update /etc/nginx/sites-enabled/default and reload")
    assert "/etc/nginx/sites-enabled/default" in paths


def test_extract_instruction_paths_rejects_start_restart_false_candidate() -> None:
    paths = extract_instruction_paths("Start/restart the service")
    assert "Start/restart" not in paths


def test_remove_absolute_path_creates_absent_path_observable(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Remove /etc/nginx/sites-enabled/default",
        task_mode="general",
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )
    obs = [o for o in contract.required_observables if o.path == "/etc/nginx/sites-enabled/default"]
    assert obs
    assert obs[0].kind == ObservableKind.ABSENT_PATH.value
    assert obs[0].purpose == "must_be_absent"


def test_absent_path_observable_passes_when_path_absent(tmp_path: Path) -> None:
    contract = TaskOutcomeContract(
        objective="remove x",
        task_mode="general",
        success_predicate="absent",
        required_observables=[
            RequiredObservable(
                kind=ObservableKind.ABSENT_PATH.value,
                path="gone.txt",
                description="must be absent",
                purpose="must_be_absent",
                strength="hard",
                confidence=0.9,
            )
        ],
    )
    result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert result.satisfied is True
