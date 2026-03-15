from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from villani_code.benchmark.models import (
    BENCHMARK_VERSION,
    BenchmarkRunResult,
    BenchmarkTrack,
    FairnessClassification,
    TaskDifficulty,
    TaskFamily,
    TaskSource,
    TelemetryQuality,
)
from villani_code.benchmark.rebuild import rebuild_results_from_directory
from villani_code.benchmark.reporting import diagnostics, load_results
from villani_code.cli import app


cli_runner = CliRunner()


def _write_task_suite(root: Path, task_id: str = "t1") -> Path:
    suite = root / "benchmark_tasks" / "villani_bench_v1" / task_id
    (suite / "repo").mkdir(parents=True)
    (suite / "prompt.txt").write_text("Fix the bug", encoding="utf-8")
    (suite / "task.yaml").write_text(
        "\n".join(
            [
                f"id: {task_id}",
                "family: bugfix",
                "difficulty: easy",
                "language: python",
                "max_minutes: 5",
                "max_files_touched: 2",
                "visible_verification:",
                "  - pytest -q",
                "hidden_verification:",
                "  - pytest -q tests/test_hidden.py",
                "success_policy:",
                "  require_visible_pass: true",
                "  require_hidden_pass: true",
                "  fail_on_timeout: true",
                "  fail_on_repo_dirty_outside_allowlist: true",
                "allowlist_paths:",
                "  - src/",
            ]
        ),
        encoding="utf-8",
    )
    (suite / "metadata.json").write_text(
        json.dumps(
            {
                "benchmark_track": "core",
                "benchmark_bucket": "baseline",
                "expected_files": ["src/app.py"],
                "task_type": "unit",
            }
        ),
        encoding="utf-8",
    )
    return suite.parent


def _sample_result(task_id: str = "t1") -> BenchmarkRunResult:
    return BenchmarkRunResult(
        benchmark_version=BENCHMARK_VERSION,
        benchmark_track=BenchmarkTrack.CORE,
        task_id=task_id,
        task_version="1.0",
        task_family=TaskFamily.BUGFIX,
        task_difficulty=TaskDifficulty.EASY,
        task_language="python",
        task_source_type=TaskSource.CURATED,
        task_tags=[],
        task_type="unit",
        benchmark_bucket="baseline",
        runtime_stressors=[],
        expected_files=["src/app.py"],
        task_checksum="abc",
        agent_name="villani",
        adapter_name="villani",
        adapter_version="1",
        adapter_capability="native",
        fairness_classification=FairnessClassification.EXACT_COMPARABLE,
        fairness_notes="ok",
        telemetry_capability="full",
        model_name="m",
        success=1,
        pass_rate=1.0,
        failed=0,
        timed_out=0,
        visible_pass=True,
        hidden_pass=True,
        runtime_seconds=2.0,
        timeout=False,
        touched_file_paths=["src/app.py"],
        files_touched=1,
        lines_added=2,
        lines_deleted=1,
        verifications_run=["pytest -q"],
        telemetry_quality=TelemetryQuality.EXACT,
        reproducibility_manifest_path="manifest_t1_0_123.json",
        repeat_index=0,
    )


def test_rebuild_from_full_result_artifact(tmp_path: Path) -> None:
    out = tmp_path / "artifacts" / "benchmark" / "run1"
    out.mkdir(parents=True)
    row = _sample_result()
    (out / "agent_debug" / "t1__r0").mkdir(parents=True)
    (out / "agent_debug" / "t1__r0" / "result.json").write_text(row.model_dump_json(indent=2), encoding="utf-8")

    summary = rebuild_results_from_directory(out, task_suite_roots=[])

    assert summary.rebuilt_count == 1
    assert summary.exact_count == 1
    rebuilt = load_results(out / "results.jsonl")
    assert rebuilt[0].task_id == "t1"
    assert (out / "summary.json").exists()
    assert (out / "results.csv").exists()


def test_rebuild_from_manifest_and_verifier_meta_is_conservative(tmp_path: Path) -> None:
    suite_root = _write_task_suite(tmp_path)
    out = tmp_path / "artifacts" / "benchmark" / "run2"
    (out / "agent_debug" / "t1__r0").mkdir(parents=True)
    (out / "manifest_t1_0_111.json").write_text(
        json.dumps(
            {
                "benchmark_version": BENCHMARK_VERSION,
                "task_id": "t1",
                "task_version": "1.0",
                "task_checksum": "abc",
                "repo_checksum": "def",
                "visible_check_checksum": "v",
                "hidden_check_checksum": "h",
                "adapter_name": "villani",
                "adapter_version": "1",
                "timeout_seconds": 10,
                "repeat_index": 0,
                "platform": "linux",
                "python_version": "3.11",
                "agent_name": "villani",
            }
        ),
        encoding="utf-8",
    )
    (out / "agent_debug" / "t1__r0" / "visible_verify_1_meta.json").write_text(
        json.dumps({"stage": "visible", "command": "pytest -q", "passed": True, "exit_code": 0}),
        encoding="utf-8",
    )

    summary = rebuild_results_from_directory(out, task_suite_roots=[suite_root])
    rows = load_results(out / "results.jsonl")

    assert summary.partial_count == 1
    assert rows[0].success == 0
    assert rows[0].visible_pass is True
    assert rows[0].hidden_pass is False
    assert rows[0].policy_warning == "reconstructed_result"


def test_rebuild_handles_corrupt_aggregate_files_and_supports_stats(tmp_path: Path) -> None:
    out = tmp_path / "artifacts" / "benchmark" / "run3"
    out.mkdir(parents=True)
    row = _sample_result()
    (out / "results.jsonl").write_text(row.model_dump_json() + "\n{not-json\n", encoding="utf-8")
    (out / "summary.json").write_text("{broken", encoding="utf-8")

    rebuild_results_from_directory(out, task_suite_roots=[])

    loaded = load_results(out / "results.jsonl")
    stats = diagnostics(loaded)
    assert len(loaded) == 1
    assert stats["summary"]["total_tasks"] == 1
    assert (out / "rebuild_meta.json").exists()


def test_cli_benchmark_rebuild_results(tmp_path: Path) -> None:
    out = tmp_path / "artifacts" / "benchmark" / "run4"
    out.mkdir(parents=True)
    row = _sample_result()
    (out / "task_result.json").write_text(row.model_dump_json(indent=2), encoding="utf-8")

    result = cli_runner.invoke(app, ["benchmark", "rebuild-results", "--dir", str(out), "--suite", str(tmp_path / "missing_suite")])

    assert result.exit_code == 0
    assert "rebuilt 1 results" in result.stdout
    assert (out / "summary.json").exists()
