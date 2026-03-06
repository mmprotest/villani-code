from __future__ import annotations

from pathlib import Path

from villani_code.autonomy import TaskContract, VerificationEngine, VerificationStatus
from villani_code.autonomous import AutonomousTask, VillaniModeController


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
        "Inspect repo for highest-leverage small improvement",
        TaskContract.INSPECTION.value,
    )
    task.inspection_summary = (
        "Checked README and package layout; no bounded safe fix needed."
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
