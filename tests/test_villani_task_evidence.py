from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from villani_code.autonomy import TaskContract, VerificationEngine, VerificationStatus
from villani_code.autonomous import AutonomousTask, VillaniModeController
from villani_code.state import Runner
from villani_code.execution import ExecutionBudget
from villani_code import state_runtime
from villani_code.task_contract import ObservableKind, RequiredObservable, TaskOutcomeContract
class StaticRunner:
    def __init__(self, execution: dict[str, object] | None = None) -> None:
        self.execution = execution or {
            "terminated_reason": "completed",
            "turns_used": 1,
            "tool_calls_used": 1,
            "elapsed_seconds": 0.01,
            "files_changed": [],
            "intentional_changes": [],
            "incidental_changes": [],
            "all_changes": [],
            "validation_artifacts": [],
            "inspection_summary": "",
            "runner_failures": [],
            "intended_targets": [],
            "before_contents": {},
        }
    def run(self, _prompt: str, **_kwargs):
        return {
            "response": {"content": [{"type": "text", "text": "done"}]},
            "transcript": {"tool_results": []},
            "execution": self.execution,
        }
def _task(title: str, contract: str) -> AutonomousTask:
    return AutonomousTask(
        "1",
        title,
        "r",
        priority=1.0,
        confidence=1.0,
        verification_plan=[],
        task_contract=contract,
    )
def test_effectful_task_with_zero_changes_cannot_pass(tmp_path: Path) -> None:
    controller = VillaniModeController(StaticRunner(), tmp_path)
    task = _task("Bootstrap minimal tests", TaskContract.EFFECTFUL.value)
    verification = controller.verifier.verify("goal", [], [], validation_artifacts=[])
    status, _ = controller._adjudicate_task(task, verification)
    assert status != "passed"
def test_validation_task_with_only_reads_cannot_pass(tmp_path: Path) -> None:
    controller = VillaniModeController(StaticRunner(), tmp_path)
    task = _task("Validate baseline importability", TaskContract.VALIDATION.value)
    verification = controller.verifier.verify("goal", [], [], validation_artifacts=[])
    status, _ = controller._adjudicate_task(task, verification)
    assert status != "passed"
def test_inspection_task_can_pass_with_concrete_inspection_summary(
    tmp_path: Path,
) -> None:
    controller = VillaniModeController(StaticRunner(), tmp_path)
    task = _task(
        "Inspect repo for highest-leverage improvement",
        TaskContract.INSPECTION.value,
    )
    task.inspection_summary = (
        "Checked README and package layout; no safe fix needed."
    )
    task.produced_inspection_conclusion = True
    verification = controller.verifier.verify(
        "goal", [], [], validation_artifacts=["inspection completed"]
    )
    status, _ = controller._adjudicate_task(task, verification)
    assert status == "passed"
def test_uncertain_verification_does_not_retire_task(tmp_path: Path) -> None:
    controller = VillaniModeController(StaticRunner(), tmp_path)
    task = _task("Audit docs drift", TaskContract.EFFECTFUL.value)
    task.intentional_changes = ["README.md"]
    task.produced_effect = True
    verification = controller.verifier.verify(
        "goal", ["README.md"], [{"command": "python -m compileall -q .", "exit": 0}]
    )
    verification.status = VerificationStatus.UNCERTAIN
    status, _ = controller._adjudicate_task(task, verification)
    assert status != "passed"
def test_zero_examined_files_is_not_clean_success(tmp_path: Path) -> None:
    engine = VerificationEngine(tmp_path)
    result = engine.verify("goal", [], [], validation_artifacts=[])
    assert result.status != VerificationStatus.PASS
    assert any(
        "No intervention or validation evidence produced." in f.message
        for f in result.findings
    )
def test_runner_failures_block_false_pass(tmp_path: Path) -> None:
    controller = VillaniModeController(StaticRunner(), tmp_path)
    task = _task("Validate baseline importability", TaskContract.VALIDATION.value)
    task.runner_failures = ["test_failure: pytest failed"]
    verification = controller.verifier.verify("goal", [], [], validation_artifacts=[])
    status, _ = controller._adjudicate_task(task, verification)
    assert status != "passed"
def test_bootstrap_tests_requires_test_file_change(tmp_path: Path) -> None:
    controller = VillaniModeController(StaticRunner(), tmp_path)
    task = _task("Bootstrap minimal tests", TaskContract.EFFECTFUL.value)
    task.intentional_changes = ["README.md"]
    task.produced_effect = True
    verification = controller.verifier.verify(
        "goal", ["README.md"], [{"command": "python -m compileall -q .", "exit": 0}]
    )
    status, _ = controller._adjudicate_task(task, verification)
    assert status != "passed"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    task.intentional_changes = ["tests/test_smoke.py"]
    task.produced_effect = True
    verification = controller.verifier.verify(
        "goal",
        ["tests/test_smoke.py"],
        [{"command": "python -m compileall -q .", "exit": 0}],
    )
    status, _ = controller._adjudicate_task(task, verification)
    assert status == "passed"
def test_validate_importability_requires_validation_artifact(tmp_path: Path) -> None:
    controller = VillaniModeController(StaticRunner(), tmp_path)
    task = _task("Validate baseline importability", TaskContract.VALIDATION.value)
    verification = controller.verifier.verify("goal", [], [], validation_artifacts=[])
    status, _ = controller._adjudicate_task(task, verification)
    assert status != "passed"
    task.validation_artifacts = ["python -c 'import villani_code' (exit=0)"]
    task.produced_validation = True
    verification = controller.verifier.verify(
        "goal", [], [], validation_artifacts=task.validation_artifacts
    )
    status, _ = controller._adjudicate_task(task, verification)
    assert status == "passed"
def test_noop_execution_gets_no_effect_outcome(tmp_path: Path) -> None:
    execution = {
        "terminated_reason": "no_edits",
        "turns_used": 3,
        "tool_calls_used": 3,
        "elapsed_seconds": 0.1,
        "files_changed": [],
        "intentional_changes": [],
        "incidental_changes": [],
        "all_changes": [],
        "validation_artifacts": [],
        "inspection_summary": "",
        "runner_failures": [],
        "intended_targets": [],
        "before_contents": {},
    }
    controller = VillaniModeController(StaticRunner(execution), tmp_path)
    task = _task("Audit tracked runtime artifacts", TaskContract.INSPECTION.value)
    controller._execute_task(task)
    assert "No intervention or validation evidence produced." in task.outcome
    assert task.status == "failed"
def test_outer_controller_does_not_override_noop_execution_to_pass(
    tmp_path: Path,
) -> None:
    controller = VillaniModeController(StaticRunner(), tmp_path)
    task = _task("Bootstrap minimal tests", TaskContract.EFFECTFUL.value)
    task.terminated_reason = "no_edits"
    verification = controller.verifier.verify("goal", [], [], validation_artifacts=[])
    status, _ = controller._adjudicate_task(task, verification)
    assert status != "passed"
def test_completion_gate_missing_required_file_blocks() -> None:
    repo = Path(".")
    dummy = SimpleNamespace(
        repo=repo,
        _task_outcome_contract=TaskOutcomeContract(
            objective="require file",
            task_mode="general",
            success_predicate="file exists",
            required_observables=[RequiredObservable(kind=ObservableKind.FILE.value, path="does/not/exist.txt", description="required")],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=[])
    assert gate["allowed"] is False
    assert gate["contract_satisfied"] is False
def test_completion_gate_existing_required_file_allows(tmp_path: Path) -> None:
    (tmp_path / "required.txt").write_text("ok\n", encoding="utf-8")
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="require file",
            task_mode="general",
            success_predicate="file exists",
            required_observables=[RequiredObservable(kind=ObservableKind.FILE.value, path="required.txt", description="required")],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=["required.txt"], validation_artifacts=[])
    assert gate["allowed"] is True
    assert gate["contract_satisfied"] is True
def test_completion_gate_empty_contract_allows(tmp_path: Path) -> None:
    dummy = SimpleNamespace(repo=tmp_path, _task_outcome_contract=TaskOutcomeContract(objective="x", task_mode="general", success_predicate="x"), _last_inspection_summary="", _inspection_summary="")
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=[])
    assert gate["allowed"] is True
def test_completion_gate_inspection_evidence_allows(tmp_path: Path) -> None:
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="inspect",
            task_mode="inspect_and_plan",
            success_predicate="inspection evidence",
            required_observables=[RequiredObservable(kind=ObservableKind.FILE.value, path="missing.txt", description="missing")],
        ),
        _last_inspection_summary="Checked files and reported findings.",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=["inspection completed"])
    assert gate["allowed"] is True
def test_completion_gate_validation_evidence_observable_allows(tmp_path: Path) -> None:
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="validate",
            task_mode="general",
            success_predicate="validation evidence",
            required_observables=[
                RequiredObservable(
                    kind=ObservableKind.VALIDATION_EVIDENCE.value,
                    path="pytest -q tests/test_state.py",
                    description="must validate",
                    purpose="must_validate",
                )
            ],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(
        dummy,
        changed_files=[],
        validation_artifacts=["pytest -q tests/test_state.py (exit=0)"],
    )
    assert gate["allowed"] is True
def test_completion_gate_repair_contract_requires_change_and_validation(tmp_path: Path) -> None:
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="repair",
            task_mode="general",
            success_predicate="repair evidence",
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
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate_only_change = state_runtime.evaluate_completion_gate(
        dummy, changed_files=["src/foo.py"], validation_artifacts=[]
    )
    assert gate_only_change["allowed"] is False
    gate_only_validation = state_runtime.evaluate_completion_gate(
        dummy,
        changed_files=[],
        validation_artifacts=["pytest -q tests/test_state.py (exit=0)"],
    )
    assert gate_only_validation["allowed"] is False
    gate_both = state_runtime.evaluate_completion_gate(
        dummy,
        changed_files=["src/foo.py"],
        validation_artifacts=["pytest -q tests/test_state.py (exit=0)"],
    )
    assert gate_both["allowed"] is True
def test_missing_generated_file_uses_specific_category(tmp_path: Path) -> None:
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="generate",
            task_mode="general",
            success_predicate="generated file exists",
            required_observables=[
                RequiredObservable(kind=ObservableKind.GENERATED_FILE.value, path="build/out.txt", description="generated")
            ],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=[])
    assert gate["allowed"] is False
    assert any(f.get("category") == "missing_generated_file" for f in gate["findings"])
def test_missing_validation_evidence_uses_specific_category(tmp_path: Path) -> None:
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="validate",
            task_mode="general",
            success_predicate="validation evidence present",
            required_observables=[
                RequiredObservable(kind=ObservableKind.VALIDATION_EVIDENCE.value, path="pytest -q", description="validation")
            ],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=[])
    assert gate["allowed"] is False
    assert any(f.get("category") == "missing_validation_evidence" for f in gate["findings"])
def test_missing_modified_file_evidence_uses_specific_category(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("print('x')\n", encoding="utf-8")
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="modify",
            task_mode="general",
            success_predicate="file changed",
            required_observables=[
                RequiredObservable(kind=ObservableKind.MODIFIED_FILE.value, path="src/foo.py", description="target", purpose="must_change")
            ],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=[])
    assert gate["allowed"] is False
    assert any(f.get("category") == "missing_change_evidence" for f in gate["findings"])


def test_completion_gate_soft_missing_observable_allows_with_validation_evidence(tmp_path: Path) -> None:
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="ref",
            task_mode="general",
            success_predicate="s",
            required_observables=[
                RequiredObservable(
                    kind=ObservableKind.FILE.value,
                    path="missing.txt",
                    description="soft",
                    strength="soft",
                    confidence=0.6,
                )
            ],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=["pytest -q (exit=0)"])
    assert gate["allowed"] is True


def test_completion_gate_hard_missing_generated_file_blocks(tmp_path: Path) -> None:
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="gen",
            task_mode="general",
            success_predicate="s",
            required_observables=[
                RequiredObservable(
                    kind=ObservableKind.GENERATED_FILE.value,
                    path="out/report.txt",
                    description="hard",
                    strength="hard",
                    confidence=0.9,
                )
            ],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=["pytest -q (exit=0)"])
    assert gate["allowed"] is False


def test_completion_gate_hard_missing_modified_file_blocks(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x=1\n", encoding="utf-8")
    dummy = SimpleNamespace(
        repo=tmp_path,
        _task_outcome_contract=TaskOutcomeContract(
            objective="mod",
            task_mode="general",
            success_predicate="s",
            required_observables=[
                RequiredObservable(
                    kind=ObservableKind.MODIFIED_FILE.value,
                    path="src/foo.py",
                    description="hard",
                    purpose="must_change",
                    strength="hard",
                    confidence=0.9,
                )
            ],
        ),
        _last_inspection_summary="",
        _inspection_summary="",
    )
    gate = state_runtime.evaluate_completion_gate(dummy, changed_files=[], validation_artifacts=["pytest -q (exit=0)"])
    assert gate["allowed"] is False
