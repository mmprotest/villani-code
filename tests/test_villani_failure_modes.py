from __future__ import annotations

from pathlib import Path

from villani_code.autonomy import FailureCategory, FailureClassifier, TaskContract, VerificationEngine
from villani_code.autonomous import AutonomousTask, VillaniModeController
from villani_code.state import Runner


class _Client:
    def create_message(self, payload, stream):
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


def _runner(tmp_path: Path) -> Runner:
    return Runner(client=_Client(), repo=tmp_path, model="m", stream=False, small_model=True)


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


def test_small_model_guard_allows_new_file_write(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    err = runner._small_model_tool_guard("Write", {"file_path": "tests/test_imports.py", "content": "x"})
    assert err is None


def test_small_model_guard_rejects_patch_for_missing_file(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    err = runner._small_model_tool_guard("Patch", {"file_path": "tests/test_imports.py", "patch": "x"})
    assert err is not None
    assert "Use Write" in err


def test_inloop_verification_uses_task_local_delta_not_global_dirty_tree(tmp_path: Path, monkeypatch) -> None:
    runner = _runner(tmp_path)
    seen: dict[str, list[str]] = {}

    def fake_verify(goal, changed_files, *args, **kwargs):
        seen["changed"] = changed_files
        class R:
            status = type("S", (), {"value": "pass"})
            confidence_score = 0.9
            findings = []
            summary = "ok"
        return R()

    runner._verification_engine.verify = fake_verify  # type: ignore[assignment]
    runner._verification_baseline_changed = {"README.md"}
    monkeypatch.setattr(runner, "_git_changed_files", lambda: ["README.md"])

    runner._run_verification("edit")

    assert seen["changed"] == []


def test_verification_confidence_not_static_for_repeated_stale_findings(tmp_path: Path) -> None:
    engine = VerificationEngine(tmp_path)
    first = engine.verify("goal", [], [], validation_artifacts=[])
    second = engine.verify("goal", [], [], validation_artifacts=[])
    assert second.repeated_verification_state is True
    assert second.confidence_score <= first.confidence_score


def test_repeated_identical_verification_triggers_no_progress_path(tmp_path: Path, monkeypatch) -> None:
    runner = _runner(tmp_path)
    events: list[dict] = []
    runner.event_callback = lambda event: events.append(event)
    runner._verification_baseline_changed = set()
    monkeypatch.setattr(runner, "_git_changed_files", lambda: [])

    runner._run_verification("edit")
    runner._run_verification("edit")
    runner._run_verification("edit")

    assert any(e.get("category") == "repeated_no_progress" for e in events)


def test_validation_task_requires_real_command_artifact(tmp_path: Path) -> None:
    controller = VillaniModeController(object(), tmp_path)
    task = _task("Validate baseline importability", TaskContract.VALIDATION.value)
    task.validation_artifacts = ["imports are working"]
    task.produced_validation = True
    verification = controller.verifier.verify("goal", [], [], validation_artifacts=[])
    status, reason = controller._adjudicate_task(task, verification)
    assert status != "passed"
    assert "validation_not_executed" in reason

    task.validation_artifacts = ["python -c 'import villani_code' (exit=0)"]
    task.produced_validation = True
    verification = controller.verifier.verify("goal", [], [], validation_artifacts=task.validation_artifacts)
    status, _ = controller._adjudicate_task(task, verification)
    assert status == "passed"


def test_read_before_edit_policy_failure_not_classified_as_test_failure() -> None:
    classifier = FailureClassifier()
    failure = classifier.classify(
        "Patch failed",
        "Read-before-edit policy: failed to auto-read tests/__init__.py",
    )
    assert failure.category == FailureCategory.TOOL_FAILURE


def test_task_summary_hides_incidental_only_changes_from_primary_changed_line(tmp_path: Path) -> None:
    summary = VillaniModeController.format_summary(
        {
            "repo_summary": "x",
            "tasks_attempted": [
                {
                    "title": "t",
                    "status": "failed",
                    "task_contract": TaskContract.EFFECTFUL.value,
                    "intentional_changes": [],
                    "incidental_changes": [".villani_code/transcripts/a.json", "__pycache__/x.pyc"],
                    "verification": [],
                }
            ],
            "done_reason": "done",
            "blockers": [],
            "files_changed": [],
            "intentional_changes": [],
            "incidental_changes": [],
            "recommended_next_steps": [],
        }
    )
    assert 'changed: []' in summary
    assert 'intentional_changed' not in summary
    assert 'incidental_changed' in summary


def test_verification_ignores_villani_transcripts_and_pycache(tmp_path: Path) -> None:
    engine = VerificationEngine(tmp_path)
    result = engine.verify(
        "goal",
        [".villani_code/transcripts/a.json", "__pycache__/x.pyc"],
        [],
        validation_artifacts=[],
    )
    assert result.files_examined == []


def test_importability_task_generates_or_requires_bounded_import_command(tmp_path: Path) -> None:
    class CaptureRunner:
        def __init__(self):
            self.prompt = ""

        def run(self, prompt: str, **_kwargs):
            self.prompt = prompt
            return {
                "response": {"content": [{"type": "text", "text": "done"}]},
                "execution": {
                    "terminated_reason": "completed",
                    "turns_used": 1,
                    "tool_calls_used": 0,
                    "elapsed_seconds": 0.1,
                    "files_changed": [],
                    "intentional_changes": [],
                    "incidental_changes": [],
                    "all_changes": [],
                    "validation_artifacts": ["python -c 'import villani_code' (exit=0)"],
                    "inspection_summary": "",
                    "runner_failures": [],
                    "intended_targets": [],
                    "before_contents": {},
                },
                "transcript": {"tool_results": []},
            }

    runner = CaptureRunner()
    controller = VillaniModeController(runner, tmp_path)
    task = _task("Validate baseline importability", TaskContract.VALIDATION.value)
    controller._execute_task(task)
    assert "python -c" in runner.prompt
    assert "No network" in runner.prompt


def test_extract_commands_reads_json_bash_output(tmp_path: Path) -> None:
    controller = VillaniModeController(object(), tmp_path)
    commands = controller._extract_commands(
        {
            "transcript": {
                "tool_results": [
                    {
                        "content": "{\"command\": \"python -c \\\"import villani_code\\\"\", \"exit_code\": 0, \"stdout\": \"\", \"stderr\": \"\"}"
                    }
                ]
            }
        }
    )
    assert commands == [{"command": 'python -c "import villani_code"', "exit": 0}]


def test_validation_task_with_successful_artifact_is_not_marked_not_executed(tmp_path: Path) -> None:
    controller = VillaniModeController(object(), tmp_path)
    task = _task("Validate baseline importability", TaskContract.VALIDATION.value)
    task.validation_artifacts = ["python -c 'import villani_code' (exit=0)"]
    task.produced_validation = controller._has_real_validation_artifact(task)
    verification = controller.verifier.verify(
        "goal",
        [],
        [{"command": "python -c 'import villani_code'", "exit": 0}],
        validation_artifacts=task.validation_artifacts,
    )

    status, reason = controller._adjudicate_task(task, verification)

    assert task.produced_validation is True
    assert status == "passed"
    assert "validation_not_executed" not in reason


def test_small_model_scope_lock_allows_one_expansion_then_blocks(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("x=0\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("y=0\n", encoding="utf-8")
    (tmp_path / "src" / "c.py").write_text("z=0\n", encoding="utf-8")
    runner = _runner(tmp_path)
    runner._intended_targets = {"src/a.py"}
    runner._files_read = {"src/b.py", "src/c.py"}

    assert runner._small_model_tool_guard("Patch", {"file_path": "src/b.py", "patch": "x"}) is None
    runner._intended_targets.add("src/b.py")
    blocked = runner._small_model_tool_guard("Patch", {"file_path": "src/c.py", "patch": "x"})
    assert blocked is not None
    assert "blocked widening" in blocked


def test_small_model_scope_lock_allows_adjacent_test_expansion(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "foo.py").write_text("x=0\n", encoding="utf-8")
    (tmp_path / "tests" / "test_foo.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    runner = _runner(tmp_path)
    runner._intended_targets = {"src/foo.py"}

    assert runner._small_model_tool_guard("Patch", {"file_path": "tests/test_foo.py", "patch": "x"}) is None


def test_small_model_guard_captures_before_contents_when_admitting(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "a.py"
    target.write_text("x=0\n", encoding="utf-8")
    runner = _runner(tmp_path)

    err = runner._small_model_tool_guard("Patch", {"file_path": "src/a.py", "patch": "x"})
    assert err is None
    assert runner._before_contents["src/a.py"] == "x=0\n"
