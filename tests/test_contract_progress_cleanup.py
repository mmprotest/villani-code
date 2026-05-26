from __future__ import annotations

from pathlib import Path

from villani_code.progress_ledger import ProgressLedger
from villani_code.task_contract import build_task_outcome_contract, check_contract_satisfaction


def test_existing_file_repair_requires_change_and_validation(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("print('ok')\n", encoding="utf-8")

    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Fix the bug in src/foo.py",
        task_mode="general",
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )

    result = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert result.satisfied is False
    assert any(f.category == "missing_change_evidence" for f in result.findings)
    assert any(f.category == "missing_validation_evidence" for f in result.findings)


def test_existing_file_repair_passes_with_change_and_validation(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("print('ok')\n", encoding="utf-8")

    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Fix the bug in src/foo.py",
        task_mode="general",
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )

    result = check_contract_satisfaction(
        tmp_path,
        contract,
        changed_files=["src/foo.py"],
        validation_artifacts=["pytest passed for src/foo.py"],
    )
    assert result.satisfied is True


def test_generated_artifact_task_requires_artifact(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Write the summary to output/summary.txt",
        task_mode="general",
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )

    missing = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert missing.satisfied is False
    assert any(f.category == "missing_generated_file" for f in missing.findings)

    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "summary.txt").write_text("done\n", encoding="utf-8")
    present = check_contract_satisfaction(tmp_path, contract, changed_files=[], validation_artifacts=[])
    assert present.satisfied is True


def test_path_extraction_ignores_dotted_non_path_text(tmp_path: Path) -> None:
    contract = build_task_outcome_contract(
        repo=tmp_path,
        instruction="Use Python 3.11 and sklearn.model_selection",
        task_mode="general",
        execution_plan=None,
        benchmark_config=None,
        existing_preferred_targets=[],
    )

    required_paths = {o.path for o in contract.required_observables if o.path}
    assert "3.11" not in required_paths
    assert "sklearn.model_selection" not in required_paths


def test_progress_ledger_ignores_cumulative_unchanged_files() -> None:
    ledger = ProgressLedger()
    ledger.record_observation(
        tool_name="Patch",
        tool_input={"file_path": "src/foo.py"},
        result_is_error=False,
        action_changed_files=["src/foo.py"],
        cumulative_changed_files=["src/foo.py"],
        validation_artifacts=[],
        verification_fingerprint="",
        contract_satisfied=False,
        contract_findings_count=2,
    )
    for _ in range(2):
        ledger.record_observation(
            tool_name="Bash",
            tool_input={"command": "pytest -q"},
            result_is_error=False,
            action_changed_files=[],
            cumulative_changed_files=["src/foo.py"],
            validation_artifacts=[],
            verification_fingerprint="",
            contract_satisfied=False,
            contract_findings_count=2,
        )

    assert ledger.assess().repeated_file_patch is False


def test_progress_ledger_catches_repeated_same_file_mutation() -> None:
    ledger = ProgressLedger()
    for _ in range(3):
        ledger.record_observation(
            tool_name="Patch",
            tool_input={"file_path": "src/foo.py"},
            result_is_error=False,
            action_changed_files=["src/foo.py"],
            cumulative_changed_files=["src/foo.py"],
            validation_artifacts=[],
            verification_fingerprint="",
            contract_satisfied=False,
            contract_findings_count=2,
        )

    assert ledger.assess().repeated_file_patch is True


def test_repeated_empty_verification_fingerprints_do_not_stall() -> None:
    ledger = ProgressLedger()
    for _ in range(3):
        ledger.record_observation(
            tool_name="Bash",
            tool_input={"command": "echo ok"},
            result_is_error=False,
            action_changed_files=[],
            cumulative_changed_files=[],
            validation_artifacts=[],
            verification_fingerprint="",
            contract_satisfied=False,
            contract_findings_count=1,
        )
    assessment = ledger.assess()
    assert assessment.repeated_verification is False
    assert assessment.stalled is False
