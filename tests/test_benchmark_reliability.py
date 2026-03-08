from __future__ import annotations

import stat
from pathlib import Path

from villani_code.benchmark.adapters.base import AdapterRunResult
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
    assert not is_runtime_artifact_path("src/app/main.py")


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
        lambda repo, commands, timeout_seconds: (
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
