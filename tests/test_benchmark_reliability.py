from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

from villani_code.benchmark.adapters.base import AdapterRunResult
from villani_code.benchmark.diff_stats import ensure_git_repo, line_stats
from villani_code.benchmark.models import FieldQuality, TelemetryQuality, VerificationOutcome
from villani_code.benchmark.policy import (
    filter_meaningful_touched_paths,
    is_runtime_artifact_path,
)
from villani_code.benchmark.runner import BenchmarkRunner
from villani_code.benchmark.policy import enforce_path_policy


def test_runtime_artifact_filtering_ignores_junk_but_keeps_real_edits() -> None:
    touched = [
        "src/app/core.py",
        ".villani_code/logs/session.json",
        ".villani/memory.md",
        "src/app/__pycache__/core.cpython-312.pyc",
        "dist/wheel.whl",
        "tests/test_core.py",
    ]
    filtered = filter_meaningful_touched_paths(touched)
    assert "src/app/core.py" in filtered
    assert "tests/test_core.py" in filtered
    assert ".villani_code/logs/session.json" not in filtered
    assert ".villani/memory.md" not in filtered
    assert "src/app/__pycache__/core.cpython-312.pyc" not in filtered
    assert "dist/wheel.whl" not in filtered


def test_runtime_artifact_matcher_patterns() -> None:
    assert is_runtime_artifact_path(".villani_code/state.json")
    assert is_runtime_artifact_path("pkg/__pycache__/mod.pyc")
    assert is_runtime_artifact_path("pkg/mod.pyc")
    assert is_runtime_artifact_path("tmp/benchmark.log")
    assert not is_runtime_artifact_path("src/app/main.py")


def test_line_stats_exclude_runtime_artifacts_from_tracked_and_untracked_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    ensure_git_repo(repo)

    (repo / "src" / "app.py").write_text("one\nthree\n", encoding="utf-8")
    (repo / ".villani_code").mkdir()
    (repo / ".villani_code" / "runtime_events.jsonl").write_text("\n".join(f"event-{idx}" for idx in range(200)), encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00\x01")
    (repo / "tmp").mkdir()
    (repo / "tmp" / "runner.log").write_text("noise\n" * 50, encoding="utf-8")

    assert line_stats(repo) == (1, 1)


def test_line_stats_ignore_untracked_runtime_artifacts_but_count_meaningful_untracked_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    ensure_git_repo(repo)

    (repo / "src" / "new_module.py").write_text("a\nb\nc\n", encoding="utf-8")
    (repo / ".villani").mkdir()
    (repo / ".villani" / "session.txt").write_text("runner noise\n" * 40, encoding="utf-8")

    assert line_stats(repo) == (3, 0)


def test_windows_safe_rmtree_removes_readonly_tree(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    file_path = root / "x.txt"
    file_path.write_text("data", encoding="utf-8")
    file_path.chmod(stat.S_IREAD)

    BenchmarkRunner._safe_rmtree(root)

    assert not root.exists()


def test_logging_and_stderr_preview_and_filtered_policy(monkeypatch, capsys) -> None:
    from villani_code.benchmark.models import FairnessClassification, TaskFamily

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="Traceback line 1\nTraceback line 2",
                exit_code=2,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: ["src/app.py", ".villani_code/state.json", "build/out.bin"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (1, 0))
    monkeypatch.setattr(
        "villani_code.benchmark.runner.run_commands",
        lambda repo, commands, timeout_seconds, **_kwargs: (
            True,
            [
                VerificationOutcome(
                    command="pytest -q",
                    passed=True,
                    exit_code=0,
                    stdout="",
                    stderr="",
                    started_at=1.0,
                    finished_at=2.0,
                )
            ],
            1.0,
            2.0,
            False,
        ),
    )

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="bugfix_001_datetime_cli",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )

    out = capsys.readouterr().out
    assert "[benchmark] start" in out
    assert "[benchmark] starting agent process..." in out
    assert "[benchmark] agent exit_code=2" in out
    assert "[benchmark] agent telemetry commands=0" in out
    assert "[benchmark] agent crash:" in out
    assert "[benchmark] result" in out
    assert "[benchmark] complete successes=" in out

    from villani_code.benchmark.reporting import load_results

    row = load_results(Path(data["results_path"]))[0]
    assert row.failure_reason is not None
    assert row.failure_reason.value == "agent_crash"
    assert row.agent_exit_code == 2
    assert row.stderr_preview is not None
    assert "Traceback" in row.stderr_preview
    assert row.touched_file_paths == ["src/app.py"]
    assert ".villani_code/state.json" in row.raw_touched_file_paths
    assert row.runtime_artifact_paths == [".villani_code/state.json", "build/out.bin"]
    assert row.files_touched == 1
    assert row.task_family == TaskFamily.BUGFIX


def test_forbidden_edit_policy_ignores_runtime_artifacts_but_flags_real_unexpected() -> None:
    allowlist = ["src/"]
    forbidden = [".git/"]

    solved_like = filter_meaningful_touched_paths(["src/app.py", ".villani_code/runtime.json", "src/__pycache__/app.pyc"])
    solved_policy = enforce_path_policy(solved_like, allowlist, forbidden)
    assert solved_policy.allowlist_ok
    assert solved_policy.forbidden_ok

    unexpected = filter_meaningful_touched_paths(["src/app.py", "tests/test_app.py"])
    unexpected_policy = enforce_path_policy(unexpected, allowlist, forbidden)
    assert not unexpected_policy.allowlist_ok


def test_missing_artifact_logging_includes_detail(monkeypatch, capsys) -> None:
    from villani_code.benchmark.models import FairnessClassification

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: [])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (0, 0))
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", lambda repo, commands, timeout_seconds, **_kwargs: (True, [], 1.0, 2.0, False))

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="bugfix_001_datetime_cli",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )

    out = capsys.readouterr().out
    assert "reason=missing_artifact" in out
    assert "detail=missing required artifact: patch" in out

    from villani_code.benchmark.reporting import load_results

    row = load_results(Path(data["results_path"]))[0]
    assert row.failure_reason is not None
    assert row.failure_reason.value == "missing_artifact"
    assert row.error is not None
    assert "missing required artifact: patch" in row.error


def test_solved_task_with_support_file_edit_is_allowed_with_warning(monkeypatch, capsys) -> None:
    from villani_code.benchmark.models import FairnessClassification

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: ["app/cli/__main__.py", "Makefile", ".villani_code/state.json"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (2, 0))
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", lambda repo, commands, timeout_seconds, **_kwargs: (True, [], 1.0, 2.0, False))

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="terminal_004_artifact_generation_pipeline",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )

    out = capsys.readouterr().out
    assert "warning=metadata_omission_reasonable detail=allowed task-related edit: app/cli/__main__.py" in out

    from villani_code.benchmark.reporting import load_results

    row = load_results(Path(data["results_path"]))[0]
    assert row.success == 1
    assert row.failure_reason is None
    assert row.policy_warning == "metadata_omission_reasonable"
    assert row.policy_warning_detail == "allowed task-related edit: app/cli/__main__.py"
    assert row.meaningful_touched_paths == ["app/cli/__main__.py", "Makefile"]
    assert row.meaningful_unexpected_paths == ["app/cli/__main__.py"]


def test_solved_task_with_unrelated_edit_still_fails_forbidden_with_detail(monkeypatch, capsys) -> None:
    from villani_code.benchmark.models import FairnessClassification

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: ["app/__main__.py", "tests/test_basic.py", "README.md"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (3, 0))
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", lambda repo, commands, timeout_seconds, **_kwargs: (True, [], 1.0, 2.0, False))

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="terminal_004_artifact_generation_pipeline",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )

    out = capsys.readouterr().out
    assert "reason=forbidden_edit" in out
    assert "detail=clearly unrelated meaningful edits: README.md" in out

    from villani_code.benchmark.reporting import load_results

    row = load_results(Path(data["results_path"]))[0]
    assert row.success == 0
    assert row.failure_reason is not None
    assert row.failure_reason.value == "forbidden_edit"
    assert row.forbidden_reason_detail == "clearly unrelated meaningful edits: README.md"
    assert row.meaningful_unexpected_paths == ["README.md"]


def test_policy_reports_unexpected_meaningful_paths() -> None:
    policy = enforce_path_policy(
        touched=["src/app.py", "tests/test_app.py"],
        allowlist=["src/", "tests/"],
        forbidden=[".git/"],
        expected_paths=["src/app.py"],
        family="bugfix",
        task_type="single_file_bugfix",
    )
    assert policy.meaningful_touched_paths == ["src/app.py", "tests/test_app.py"]
    assert policy.meaningful_expected_paths == ["src/app.py"]
    assert policy.meaningful_unexpected_paths == ["tests/test_app.py"]
    assert policy.violating_paths == ["tests/test_app.py"]
    assert policy.forbidden_reason_detail == "clearly unrelated meaningful edits: tests/test_app.py"


def test_solved_task_with_metadata_omission_edit_is_allowed_with_warning(monkeypatch, capsys) -> None:
    from villani_code.benchmark.models import FairnessClassification

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: ["src/app/a.py", "src/app/aliases.py", ".villani_code/state.json"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (2, 0))
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", lambda repo, commands, timeout_seconds, **_kwargs: (True, [], 1.0, 2.0, False))

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="localize_004_command_alias_registration",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )

    out = capsys.readouterr().out
    assert "warning=support_file_edits detail=allowed support edits: src/app/aliases.py" in out

    from villani_code.benchmark.reporting import load_results

    row = load_results(Path(data["results_path"]))[0]
    assert row.success == 1
    assert row.failure_reason is None
    assert row.policy_warning == "support_file_edits"
    assert row.policy_warning_detail == "allowed support edits: src/app/aliases.py"
    assert row.meaningful_unexpected_paths == ["src/app/aliases.py"]


def test_terminal_workflow_policy_classifies_metadata_omission_paths() -> None:
    policy = enforce_path_policy(
        touched=["app/__main__.py", "pyproject.toml"],
        allowlist=["app/", "tests/", "Makefile"],
        forbidden=[".git/"],
        expected_paths=["app/__main__.py", "Makefile", "tests/test_basic.py"],
        family="terminal_workflow",
        task_type="single_file_bugfix",
    )
    assert policy.allowed_support_paths == ["pyproject.toml"]
    assert policy.metadata_omission_paths == []
    assert policy.violating_paths == []


def test_solved_task_with_expected_file_outside_allowlist_still_succeeds(monkeypatch) -> None:
    from villani_code.benchmark.models import FairnessClassification

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: ["app/__main__.py", "pyproject.toml"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (2, 0))
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", lambda repo, commands, timeout_seconds, **_kwargs: (True, [], 1.0, 2.0, False))

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="terminal_005_lint_invocation",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )

    from villani_code.benchmark.reporting import load_results

    row = load_results(Path(data["results_path"]))[0]
    assert row.success == 1
    assert row.failure_reason is None
    assert row.meaningful_unexpected_paths == []


def test_path_classifier_categories_and_diagnostics() -> None:
    policy = enforce_path_policy(
        touched=["./src/app/a.py", "src/app/__init__.py", "src/app/helpers.py", ".villani_code/state.json", "README.md"],
        allowlist=["src/", "tests/"],
        forbidden=[".git/"],
        expected_paths=["src/app/a.py"],
        family="bugfix",
        task_type="single_file_bugfix",
    )
    assert policy.path_classifications["src/app/a.py"] == "exact_expected"
    assert policy.path_classifications["src/app/__init__.py"] == "task_adjacent_support"
    assert policy.path_classifications["src/app/helpers.py"] == "metadata_omission_reasonable"
    assert policy.path_classifications[".villani_code/state.json"] == "ignored_runtime_artifact"
    assert policy.path_classifications["README.md"] == "clearly_unrelated_meaningful_edit"
    assert "src/app/a.py" in policy.normalized_touched_paths
    assert policy.violating_paths == ["README.md"]


def test_metadata_omission_is_warning_not_failure_for_solved_task(monkeypatch) -> None:
    from villani_code.benchmark.models import FairnessClassification

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: ["src/app/a.py", "src/app/extra.py"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (2, 0))
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", lambda repo, commands, timeout_seconds, **_kwargs: (True, [], 1.0, 2.0, False))

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="bugfix_001_datetime_cli",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )

    from villani_code.benchmark.reporting import load_results

    row = load_results(Path(data["results_path"]))[0]
    assert row.success == 1
    assert row.policy_warning == "metadata_omission_reasonable"
    assert row.path_classifications["src/app/extra.py"] == "metadata_omission_reasonable"


def test_logs_visible_and_hidden_verification_steps(monkeypatch, capsys) -> None:
    from villani_code.benchmark.models import FairnessClassification

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: ["src/app/date_utils.py", "tests/test_cli_datetime.py"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (2, 1))

    calls = {"count": 0}

    def fake_run_commands(repo, commands, timeout_seconds, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return True, [VerificationOutcome(command="pytest -q tests/test_cli_datetime.py", passed=True, exit_code=0, stdout="", stderr="", started_at=1.0, finished_at=2.0)], 1.0, 2.0, False
        return True, [VerificationOutcome(command="pytest -q tests/test_cli_datetime.py", passed=True, exit_code=0, stdout="", stderr="", started_at=2.5, finished_at=3.0)], 2.5, 3.0, False

    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", fake_run_commands)

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="bugfix_001_datetime_cli",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )

    out = capsys.readouterr().out
    assert "[benchmark] running visible verification commands (1)" in out
    assert "[benchmark] visible verify [pass] code=0" in out
    assert "[benchmark] running hidden verification commands (1)" in out
    assert "[benchmark] hidden verify [pass] code=0" in out


def test_noop_patch_attempt_detection_helper() -> None:
    assert BenchmarkRunner._is_noop_patch_attempt(file_writes=0, patch_attempts=0, meaningful_changed_files=[]) is True
    assert BenchmarkRunner._is_noop_patch_attempt(file_writes=None, patch_attempts=None, meaningful_changed_files=[]) is True
    assert BenchmarkRunner._is_noop_patch_attempt(file_writes=1, patch_attempts=0, meaningful_changed_files=[]) is False
    assert BenchmarkRunner._is_noop_patch_attempt(file_writes=0, patch_attempts=0, meaningful_changed_files=["src/app.py"]) is False


def test_noop_run_is_labeled_benchmark_no_patch_attempt(monkeypatch) -> None:
    from villani_code.benchmark.models import FailureReason, FairnessClassification
    from villani_code.benchmark.reporting import load_results

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: [])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (0, 0))

    def _failing_verify(repo, commands, timeout_seconds, **_kwargs):
        return False, [
            VerificationOutcome(
                command="pytest -q",
                passed=False,
                exit_code=1,
                stdout="",
                stderr="assert failed",
                started_at=1.0,
                finished_at=2.0,
            )
        ], 1.0, 2.0, False

    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", _failing_verify)

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="bugfix_001_datetime_cli",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )
    row = load_results(Path(data["results_path"]))[0]
    assert row.success == 0
    assert row.failure_reason == FailureReason.BENCHMARK_NO_PATCH_ATTEMPT


def test_meaningful_patch_attempt_keeps_visible_verification_failed(monkeypatch) -> None:
    from villani_code.benchmark.models import FailureReason, FairnessClassification
    from villani_code.benchmark.reporting import load_results

    class FakeRunner:
        name = "villani"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: ["src/app/cli.py"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (1, 0))

    def _failing_verify(repo, commands, timeout_seconds, **_kwargs):
        return False, [
            VerificationOutcome(
                command="pytest -q",
                passed=False,
                exit_code=1,
                stdout="",
                stderr="assert failed",
                started_at=1.0,
                finished_at=2.0,
            )
        ], 1.0, 2.0, False

    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", _failing_verify)

    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="bugfix_001_datetime_cli",
        agent="villani",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )
    row = load_results(Path(data["results_path"]))[0]
    assert row.success == 0
    assert row.failure_reason == FailureReason.VISIBLE_VERIFICATION_FAILED


def test_noop_run_writes_debug_artifacts_with_redaction(monkeypatch, tmp_path: Path, capsys) -> None:
    from villani_code.benchmark.agents.base import AgentRunner
    from villani_code.benchmark.models import FailureReason, FairnessClassification
    from villani_code.benchmark.reporting import load_results

    class FakeRunner(AgentRunner):
        name = "claude-code"
        version = "1"
        capability = "cli_wrapper"
        telemetry_capability = "coarse_process_only"
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = "fake"
        supports_model_override = True

        def build_command(self, repo_path, prompt, model, base_url, api_key, provider, benchmark_config_json=None):
            return [sys.executable, "-c", "import sys; print('stdout hello'); print('stderr hello', file=sys.stderr)"]

        def build_env(self, *, base_url, api_key):
            env = super().build_env(base_url=base_url, api_key=api_key)
            env["ANTHROPIC_API_KEY"] = api_key or "sk-ant-secret"
            env["ANTHROPIC_BASE_URL"] = base_url or "http://example.invalid"
            return env

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda agent: FakeRunner())
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda repo: [])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda repo: (0, 0))

    def _failing_verify(repo, commands, timeout_seconds, **_kwargs):
        return False, [
            VerificationOutcome(
                command="pytest -q",
                passed=False,
                exit_code=1,
                stdout="",
                stderr="assert failed",
                started_at=1.0,
                finished_at=2.0,
            )
        ], 1.0, 2.0, False

    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", _failing_verify)

    runner = BenchmarkRunner(output_dir=tmp_path / "benchmark-output")
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="bugfix_001_datetime_cli",
        agent="claude-code",
        model="tiny-model",
        base_url="http://127.0.0.1:8080",
        api_key="sk-ant-secret",
    )

    out = capsys.readouterr().out
    assert "agent debug artifacts stdout=" in out
    assert "no-op output preview stdout=stdout hello" in out

    row = load_results(Path(data["results_path"]))[0]
    assert row.failure_reason == FailureReason.BENCHMARK_NO_PATCH_ATTEMPT

    debug_dir = tmp_path / "benchmark-output" / "agent_debug" / "bugfix_001_datetime_cli__r0"
    assert (debug_dir / "agent_command.txt").exists()
    assert (debug_dir / "agent_stdout.txt").exists()
    assert (debug_dir / "agent_stderr.txt").exists()
    meta_path = debug_dir / "agent_run_meta.json"
    assert meta_path.exists()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["env"]["ANTHROPIC_API_KEY"] == "[REDACTED]"
    assert meta["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"



def test_mutation_denial_summary_logged_and_surfaced(monkeypatch, capsys) -> None:
    from villani_code.benchmark.adapters.base import AdapterEvent
    from villani_code.benchmark.models import FairnessClassification

    class FakeRunner:
        name = 'villani'
        version = '1'
        capability = 'cli_wrapper'
        telemetry_capability = 'coarse_process_only'
        fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
        fairness_notes = 'fake'
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(
                stdout='',
                stderr='',
                exit_code=0,
                timeout=False,
                runtime_seconds=0.1,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={'num_shell_commands': FieldQuality.INFERRED},
                events=[
                    AdapterEvent(
                        type='benchmark_patch_blocked',
                        timestamp=1.0,
                        payload={
                            'type': 'benchmark_patch_blocked',
                            'paths': ['docs/readme.md'],
                            'reason': 'benchmark_policy_denied: task_id=t1 reason=outside_allowlist path=docs/readme.md',
                        },
                    )
                ],
            )

    monkeypatch.setattr('villani_code.benchmark.runner.build_agent_runner', lambda agent: FakeRunner())
    monkeypatch.setattr('villani_code.benchmark.runner.list_touched_files', lambda repo: [])
    monkeypatch.setattr('villani_code.benchmark.runner.line_stats', lambda repo: (0, 0))
    monkeypatch.setattr('villani_code.benchmark.runner.run_commands', lambda repo, commands, timeout_seconds, **_kwargs: (False, [], 1.0, 2.0, False))

    runner = BenchmarkRunner(output_dir=Path('artifacts/benchmark-test'))
    data = runner.run(
        suite_dir=Path('benchmark_tasks/villani_bench_v1'),
        task_id='bugfix_001_datetime_cli',
        agent='villani',
        model='tiny-model',
        base_url=None,
        api_key=None,
    )

    out = capsys.readouterr().out
    assert 'benchmark mutation denials count=1 first_path=docs/readme.md' in out

    from villani_code.benchmark.reporting import load_results

    row = load_results(Path(data['results_path']))[0]
    assert row.policy_warning == 'benchmark_mutation_denials'
    assert row.policy_warning_detail is not None
    assert 'count=1' in row.policy_warning_detail
