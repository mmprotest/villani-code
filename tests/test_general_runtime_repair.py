from __future__ import annotations

import json
from pathlib import Path

import pytest

from villani_code.permissions import Decision
from villani_code.repair import execute_repair_loop
from villani_code.state import Runner
from villani_code import state_runtime
from villani_code.state_tooling import execute_tool_with_policy
from villani_code.validation_loop import (
    ValidationEscalationPolicy,
    ValidationFailureSummary,
    ValidationPlan,
    ValidationPlanStep,
    ValidationResult,
    ValidationRunSummary,
    ValidationScope,
    ValidationSelectionReason,
    ValidationStep,
    ValidationStepResult,
    ValidationTarget,
    classify_environment_failure,
    run_validation,
)


class _Client:
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = responses or [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}]
        self.calls = 0

    def create_message(self, payload, stream):
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


def _runner(tmp_path: Path, responses: list[dict] | None = None) -> Runner:
    runner = Runner(client=_Client(responses), repo=tmp_path, model="m", stream=False, plan_mode="off")
    runner.hooks = type("Hooks", (), {"run_event": staticmethod(lambda *_a, **_k: type("H", (), {"allow": True, "reason": ""})())})()
    runner.permissions = type("Perms", (), {"evaluate_with_reason": staticmethod(lambda *_a, **_k: type("P", (), {"decision": Decision.ALLOW, "reason": ""})())})()
    runner._execution_plan = type(
        "Plan",
        (),
        {
            "task_goal": "fix app behavior",
            "change_impact": "source_only",
            "action_classes": [],
            "task_mode": type("TaskModeObj", (), {"value": "general"})(),
            "to_human_text": staticmethod(lambda: "fix app behavior"),
        },
    )()
    return runner


def _validation_result(*, passed: bool, concise: str = "AssertionError", failure_class: str = "assertion_mismatch", broaden: bool = True) -> ValidationResult:
    step = ValidationStep("pytest-targeted", "python -m pytest -q tests/test_app.py", "test", 1, False, scope_hint="targeted")
    plan = ValidationPlan(
        scope=ValidationScope(["src/app.py"], False, False, False, False, False, ["tests/test_app.py"], ["src/app.py"]),
        selected_steps=[ValidationPlanStep(step=step, command=step.command, reasons=["targeted first"])],
        reasons=[ValidationSelectionReason(step_name=step.name, reason="targeted first")],
        targets=[ValidationTarget(path="tests/test_app.py", target_type="test_file", confidence=0.95)],
        escalation=ValidationEscalationPolicy(broaden_after_targeted_pass=broaden, force_broad=False, reason="targeted_then_broaden"),
    )
    results = [ValidationStepResult(step, step.command, 0 if passed else 1, "", "" if passed else concise, 0.01)]
    failure = None
    summary = ""
    if not passed:
        failure = ValidationFailureSummary(
            step_name=step.name,
            failure_class=failure_class,
            headline="pytest-targeted failed",
            relevant_paths=["src/app.py", "tests/test_app.py"],
            relevant_error_lines=[concise],
            concise_summary=concise,
            recommended_repair_scope="targeted",
            compact_output=concise,
        )
        summary = concise
    return ValidationResult(
        passed=passed,
        plan=plan,
        steps=results,
        failure_summary=summary,
        structured_failure=failure,
        run_summary=ValidationRunSummary(passed=passed, executed_steps=[step.name], escalation_applied=False),
    )


def test_general_runtime_repair_counts_real_second_attempt_and_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
    responses = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "repair-1", "name": "Patch", "input": {"unified_diff": diff}},
                {"type": "text", "text": "repair applied"},
            ],
        }
    ]
    runner = _runner(tmp_path, responses)

    sequence = iter(
        [
            _validation_result(passed=False, concise="AssertionError: expected 2"),
            _validation_result(passed=True, broaden=True),
            _validation_result(passed=True, broaden=True),
        ]
    )
    monkeypatch.setattr("villani_code.state_runtime.run_validation", lambda *args, **kwargs: next(sequence))
    monkeypatch.setattr("villani_code.repair.run_validation", lambda *args, **kwargs: next(sequence))

    message = state_runtime.run_post_execution_validation(runner, ["src/app.py"])
    telemetry = runner._runtime_telemetry.finalize(completed=True, terminated_reason="completed")
    events_path = tmp_path / ".villani_code" / "runtime_events.jsonl"

    assert "recovered" in message.lower()
    assert telemetry["retries_after_failure"] >= 1
    assert telemetry["recovered_after_failed_attempt"] is True
    assert telemetry["first_pass_success"] is False
    assert events_path.exists()
    assert "repair_patch_cycle_completed" in events_path.read_text(encoding="utf-8")


def test_repair_no_patch_records_reason_and_does_not_count_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    runner = _runner(tmp_path, [{"role": "assistant", "content": [{"type": "text", "text": "I have no patch"}]}])

    monkeypatch.setattr("villani_code.repair.run_validation", lambda *args, **kwargs: _validation_result(passed=False, concise="should not rerun"))
    outcome = execute_repair_loop(
        runner,
        tmp_path,
        ["src/app.py"],
        _validation_result(passed=False, concise="AssertionError"),
        {"source_roots": ["src"], "test_roots": ["tests"]},
        "source_only",
        [],
        max_attempts=1,
    )
    telemetry = runner._runtime_telemetry.finalize(completed=False, terminated_reason="repair_exhausted")

    assert outcome.recovered is False
    assert telemetry["retries_after_failure"] == 0
    assert telemetry["no_patch_reason"] == "no_patch_produced_after_failed_verification"


def test_targeted_verification_is_preferred_and_broad_only_runs_after_targeted_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".villani").mkdir()
    (tmp_path / ".villani" / "validation.json").write_text(
        json.dumps(
            {
                "version": 1,
                "steps": [
                    {"name": "pytest-targeted", "command": "python -m pytest -q", "kind": "test", "cost_level": 1, "is_mutating": False, "enabled": True, "scope_hint": "targeted"},
                    {"name": "pytest", "command": "python -m pytest", "kind": "test", "cost_level": 2, "is_mutating": False, "enabled": True, "scope_hint": "repo"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".villani" / "repo_map.json").write_text(json.dumps({"test_roots": ["tests"]}), encoding="utf-8")

    commands: list[str] = []

    class Proc:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    def fake_run(command, *args, **kwargs):
        commands.append(command)
        return Proc(0)

    monkeypatch.setattr("villani_code.validation_loop.subprocess.run", fake_run)
    result = run_validation(tmp_path, ["src/app.py"], repo_map={"test_roots": ["tests"]})

    assert result.passed is True
    assert commands[0].startswith("python -m pytest -q")
    assert len(commands) == 1


def test_repeated_identical_verifier_command_without_edit_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".villani").mkdir()
    (tmp_path / ".villani" / "validation.json").write_text(
        json.dumps(
            {
                "version": 1,
                "steps": [
                    {"name": "pytest-targeted", "command": "python -m pytest -q", "kind": "test", "cost_level": 1, "is_mutating": False, "enabled": True, "scope_hint": "targeted"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".villani" / "repo_map.json").write_text(json.dumps({"test_roots": ["tests"]}), encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("villani_code.validation_loop.subprocess.run", lambda *args, **kwargs: Proc())
    cache: dict[str, int] = {}
    first = run_validation(tmp_path, ["src/app.py"], repo_map={"test_roots": ["tests"]}, command_cache=cache, edit_generation=1)
    second = run_validation(tmp_path, ["src/app.py"], repo_map={"test_roots": ["tests"]}, command_cache=cache, edit_generation=1)

    assert first.passed is True
    assert second.passed is False
    assert second.structured_failure is not None
    assert second.structured_failure.failure_class == "repeated_verifier_without_edit"


def test_generated_files_are_blocked_without_overlay_data(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    blocked = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "build/generated.py", "content": "x = 1\n"},
        "1",
        0,
    )
    assert blocked["is_error"] is True
    assert "generated_or_runtime_artifact" in blocked["content"]


def test_low_authority_files_require_runtime_evidence_in_normal_run(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    blocked = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "pyproject.toml", "content": "[project]\nname='x'\n"},
        "1",
        0,
    )
    assert blocked["is_error"] is True
    assert "low_authority_without_runtime_evidence" in blocked["content"]


def test_package_init_localization_requires_explicit_evidence(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    diagnosis = {"target_file": "pkg/__init__.py", "bug_class": "import bug", "fix_intent": "export change"}
    confidence = state_runtime.classify_diagnosis_target_confidence(runner, diagnosis, failure_evidence=None)
    assert confidence == "weak"


def test_environment_failures_do_not_trigger_blind_repair(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    runner = _runner(tmp_path)
    outcome = execute_repair_loop(
        runner,
        tmp_path,
        ["src/app.py"],
        _validation_result(passed=False, concise="ModuleNotFoundError: No module named 'app'", failure_class="src_layout_import_error"),
        {"source_roots": ["src"], "test_roots": ["tests"]},
        "source_only",
        [],
        max_attempts=2,
    )
    assert classify_environment_failure("python -m pytest -q", "", "ModuleNotFoundError: No module named 'app'", tmp_path) == "src_layout_import_error"
    assert outcome.environment_harness_failure is True
    assert outcome.recovered is False
