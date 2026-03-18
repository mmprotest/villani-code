from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.localization import build_benchmark_localization_pack, classify_verification_failure
from villani_code.permissions import Decision
from villani_code.prompting import build_system_blocks
from villani_code.repair import execute_repair_loop
from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy
from villani_code.validation_loop import (
    ValidationEscalationPolicy,
    ValidationFailureSummary,
    ValidationPlan,
    ValidationRunSummary,
    ValidationScope,
    ValidationSelectionReason,
    ValidationStep,
    ValidationStepResult,
    ValidationTarget,
    ValidationPlanStep,
    ValidationResult,
    classify_environment_failure,
)


class _Client:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def create_message(self, payload, stream):
        self.payloads.append(payload)
        return {"role": "assistant", "content": [{"type": "text", "text": "repair text"}]}


def _benchmark_config() -> BenchmarkRuntimeConfig:
    return BenchmarkRuntimeConfig(
        enabled=True,
        task_id="bench_1",
        allowlist_paths=["src/", "tests/", "pyproject.toml"],
        forbidden_paths=[".git/"],
        expected_files=["src/app.py"],
        allowed_support_files=["tests/test_app.py", "pyproject.toml"],
        max_files_touched=2,
        visible_verification=["python -m pytest -q tests/test_app.py"],
    )


def _runner(tmp_path: Path) -> Runner:
    runner = Runner(client=_Client(), repo=tmp_path, model="m", stream=False, small_model=False, benchmark_config=_benchmark_config(), plan_mode="off")
    runner.hooks = type("Hooks", (), {"run_event": staticmethod(lambda *_a, **_k: type("H", (), {"allow": True, "reason": ""})())})()
    runner.permissions = type("Perms", (), {"evaluate_with_reason": staticmethod(lambda *_a, **_k: type("P", (), {"decision": Decision.ALLOW, "reason": ""})())})()
    return runner


def _validation_result(*, passed: bool, failure_class: str = "assertion_mismatch", concise: str = "assert value", broaden: bool = True) -> ValidationResult:
    step = ValidationStep("pytest-targeted", "python -m pytest -q tests/test_app.py", "test", 1, False, scope_hint="targeted")
    plan = ValidationPlan(
        scope=ValidationScope(["src/app.py"], False, False, False, False, False, [], ["src/app.py"]),
        selected_steps=[ValidationPlanStep(step=step, command=step.command, reasons=["targeted first"])],
        reasons=[ValidationSelectionReason(step_name=step.name, reason="targeted first")],
        targets=[ValidationTarget(path="tests/test_app.py", target_type="test_file", confidence=0.9)],
        escalation=ValidationEscalationPolicy(broaden_after_targeted_pass=broaden, force_broad=False, reason="targeted_then_broaden"),
    )
    steps = [ValidationStepResult(step, step.command, 0 if passed else 1, "", "" if passed else concise, 0.01)]
    failure = None
    summary = ""
    if not passed:
        failure = ValidationFailureSummary(
            step_name=step.name,
            failure_class=failure_class,
            headline="pytest-targeted failed",
            relevant_paths=["src/app.py"],
            relevant_error_lines=[concise],
            concise_summary=concise,
            recommended_repair_scope="targeted",
            compact_output=concise,
        )
        summary = concise
    return ValidationResult(
        passed=passed,
        plan=plan,
        steps=steps,
        failure_summary=summary,
        structured_failure=failure,
        run_summary=ValidationRunSummary(passed=passed, executed_steps=[step.name], escalation_applied=False),
    )


def test_benchmark_mode_injects_repo_context_without_small_model(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_run():\n    assert True\n", encoding="utf-8")

    runner = _runner(tmp_path)
    runner._ensure_project_memory_and_plan("Fix app behavior")

    blocks = build_system_blocks(
        tmp_path,
        repo_map=runner._repo_map,
        benchmark_config=runner.benchmark_config,
        task_mode=runner._task_mode,
        benchmark_localization_pack=runner._benchmark_localization_pack,
    )
    text = "\n".join(block["text"] for block in blocks)
    assert "<repo-map>" in text
    assert "<benchmark-localization-pack>" in text
    assert "src/app.py" in text


def test_localization_ranking_prefers_expected_and_verifier_files_over_config(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def handle_request():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_handle_request():\n    assert True\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    repo_map = {"source_roots": ["src"], "test_roots": ["tests"], "config_files": ["pyproject.toml"]}
    failure = classify_verification_failure("AssertionError in src/app.py", relevant_paths=["src/app.py"])

    pack = build_benchmark_localization_pack(tmp_path, "Fix app request behavior", repo_map, _benchmark_config(), failure=failure)

    assert pack.top_candidate_files[0].path == "src/app.py"
    assert all(candidate.path != "pyproject.toml" or candidate.authority_tier >= 5 for candidate in pack.top_candidate_files)


def test_config_build_file_edits_blocked_without_explicit_verifier_evidence(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    runner = _runner(tmp_path)
    runner._benchmark_localization_pack = build_benchmark_localization_pack(
        tmp_path,
        "Fix src app logic",
        {"source_roots": ["src"], "test_roots": ["tests"], "config_files": ["pyproject.toml"]},
        runner.benchmark_config,
    )

    blocked = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "pyproject.toml", "content": "[project]\nname='y'\n"},
        "1",
        0,
    )
    assert blocked["is_error"] is True
    assert "low_authority_without_evidence" in blocked["content"]


def test_failed_verification_triggers_structured_repair_mode_and_bounded_branching(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    runner = _runner(tmp_path)
    runner._benchmark_localization_pack = build_benchmark_localization_pack(
        tmp_path,
        "Fix app logic",
        {"source_roots": ["src"], "test_roots": ["tests"]},
        runner.benchmark_config,
    )
    events: list[dict] = []
    runner.event_callback = events.append

    responses = iter(
        [
            _validation_result(passed=False, concise="AssertionError: expected 1 got 2"),
            _validation_result(passed=False, concise="AssertionError: expected 1 got 2"),
            _validation_result(passed=False, concise="AssertionError: expected 1 got 2"),
        ]
    )

    monkeypatch.setattr("villani_code.repair.run_validation", lambda *args, **kwargs: next(responses))
    outcome = execute_repair_loop(
        runner,
        tmp_path,
        ["src/app.py"],
        _validation_result(passed=False, concise="AssertionError: expected 1 got 2"),
        {"source_roots": ["src"], "test_roots": ["tests"]},
        "source_only",
        [],
        max_attempts=4,
    )

    assert outcome.recovered is False
    assert outcome.branch_count <= 2
    assert any(event.get("type") == "repair_mode_entered" for event in events)
    assert any(event.get("type") == "repair_branching_started" for event in events)


def test_targeted_verification_runs_before_broader_verification(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    runner = _runner(tmp_path)
    runner._benchmark_localization_pack = build_benchmark_localization_pack(
        tmp_path,
        "Fix app logic",
        {"source_roots": ["src"], "test_roots": ["tests"]},
        runner.benchmark_config,
    )
    calls: list[list[str] | None] = []

    def fake_run_validation(_repo, _changed, **kwargs):
        calls.append(kwargs.get("steps_override"))
        if kwargs.get("steps_override"):
            return _validation_result(passed=True, broaden=True)
        return _validation_result(passed=True, broaden=True)

    monkeypatch.setattr("villani_code.repair.run_validation", fake_run_validation)
    outcome = execute_repair_loop(
        runner,
        tmp_path,
        ["src/app.py"],
        _validation_result(passed=False, concise="AssertionError: expected 1 got 2", broaden=True),
        {"source_roots": ["src"], "test_roots": ["tests"]},
        "source_only",
        [],
        max_attempts=2,
    )

    assert outcome.recovered is True
    assert calls[0] == ["pytest-targeted"]
    assert calls[1] is None


def test_environment_failure_classification_covers_missing_make_and_src_layout_import(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    assert classify_environment_failure("make test", "", "make: not found", tmp_path) == "missing_make"
    assert classify_environment_failure("python -m pytest -q", "", "ModuleNotFoundError: No module named 'app'", tmp_path) == "src_layout_import_error"


def test_repair_prompt_inherits_benchmark_constraints_and_localization_pack(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    runner = _runner(tmp_path)
    runner._task_contract = {"success_predicate": "fix app", "preferred_targets": ["src/app.py"]}
    runner._benchmark_localization_pack = build_benchmark_localization_pack(
        tmp_path,
        "Fix app logic",
        {"source_roots": ["src"], "test_roots": ["tests"]},
        runner.benchmark_config,
    )

    monkeypatch.setattr("villani_code.repair.run_validation", lambda *args, **kwargs: _validation_result(passed=False, concise="AssertionError"))
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

    payload = runner.client.payloads[0]
    system_text = "\n".join(block["text"] for block in payload["system"])
    user_text = payload["messages"][-1]["content"][-1]["text"]
    assert outcome.failure_classification == "assertion_mismatch"
    assert "benchmark-localization-pack" in system_text
    assert "blocked_paths" in user_text
    assert "expected_files" in user_text


def test_environment_harness_failures_do_not_thrash_repair(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    outcome = execute_repair_loop(
        runner,
        tmp_path,
        ["src/app.py"],
        _validation_result(passed=False, failure_class="src_layout_import_error", concise="ModuleNotFoundError: No module named 'app'"),
        {"source_roots": ["src"], "test_roots": ["tests"]},
        "source_only",
        [],
        max_attempts=2,
    )
    assert outcome.environment_harness_failure is True
    assert outcome.recovered is False
