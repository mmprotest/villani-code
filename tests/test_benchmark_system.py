from __future__ import annotations

from pathlib import Path

import pytest

from villani_code.benchmark.diff_stats import ensure_git_repo, line_stats, list_touched_files
from villani_code.benchmark.health import run_healthcheck
from villani_code.benchmark.models import BenchmarkTrack, FieldQuality, TaskSource, TelemetryQuality
from villani_code.benchmark.reporting import diagnostics, paired_compare, render_summary_table, summarize
from villani_code.benchmark.runner import BenchmarkRunner
from villani_code.benchmark.stats import wilson_interval
from villani_code.benchmark.task_loader import TaskLoadError, load_task, load_tasks
from villani_code.benchmark.verifier import run_commands
from villani_code.benchmark.workspace import WorkspaceManager


def test_task_loader_parses_valid_task() -> None:
    task = load_task(Path("benchmark_tasks/villani_bench_v1/bugfix_001_datetime_cli"))
    assert task.id == "bugfix_001_datetime_cli"
    assert task.benchmark_track == BenchmarkTrack.CORE
    assert task.source_type in {TaskSource.CURATED, TaskSource.SEEDED, TaskSource.MUTATED}
    assert len(task.task_checksum or "") > 5


def test_task_loader_rejects_invalid_prompt(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "repo").mkdir()
    (task_dir / "prompt.txt").write_text("line1\nline2\n", encoding="utf-8")
    (task_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (task_dir / "task.yaml").write_text(
        "id: x\nfamily: bugfix\ndifficulty: easy\nlanguage: python\nmax_minutes: 1\nmax_files_touched: 1\nexpected_artifacts: [patch]\nvisible_verification: ['true']\nhidden_verification: ['true']\nsuccess_policy: {require_visible_pass: true, require_hidden_pass: true, fail_on_timeout: true, fail_on_repo_dirty_outside_allowlist: true}\nallowlist_paths: ['src/']\n",
        encoding="utf-8",
    )
    with pytest.raises(TaskLoadError):
        load_task(task_dir)


def test_workspace_copy_is_isolated(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("x", encoding="utf-8")
    manager = WorkspaceManager()
    with manager.create(source) as copied:
        (copied / "a.txt").write_text("y", encoding="utf-8")
    assert (source / "a.txt").read_text(encoding="utf-8") == "x"


def test_workspace_keep_behavior(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("x", encoding="utf-8")
    manager = WorkspaceManager(keep_workspace=True)
    with manager.create(source) as copied:
        root = copied.parent
    assert root.exists()


def test_visible_and_hidden_checks_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    passed, outcomes, _, _ = run_commands(repo, ["python -c 'print(1)'"], timeout_seconds=5)
    assert passed
    assert outcomes[0].passed


def test_allowlist_and_diff_stats(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    ensure_git_repo(repo)
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    touched = list_touched_files(repo)
    added, deleted = line_stats(repo)
    assert "tests/test_x.py" in touched
    assert added > 0
    assert deleted == 0


def test_run_emits_new_fields() -> None:
    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="terminal_001_python_module_entry",
        agent='cmd:python -c "from pathlib import Path; Path(\'app/__main__.py\').write_text(\'print(1)\\n\', encoding=\'utf-8\')"',
        model=None,
        base_url=None,
        api_key=None,
    )
    from villani_code.benchmark.reporting import load_results

    rows = load_results(Path(data["results_path"]))
    assert rows[0].adapter_name in {"cmd", "villani", "claude", "opencode", "copilot-cli"}
    assert rows[0].telemetry_quality in {TelemetryQuality.EXACT, TelemetryQuality.INFERRED, TelemetryQuality.UNAVAILABLE}
    assert rows[0].telemetry_field_quality_map.get("num_shell_commands") in {FieldQuality.EXACT, FieldQuality.INFERRED, FieldQuality.UNAVAILABLE}


def test_summary_generation_and_stats() -> None:
    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="terminal_001_python_module_entry",
        agent='cmd:python -c "from pathlib import Path; Path(\'app/__main__.py\').write_text(\'print(1)\\n\', encoding=\'utf-8\')"',
        model=None,
        base_url=None,
        api_key=None,
        repeat=2,
    )
    from villani_code.benchmark.reporting import load_results

    rows = load_results(Path(data["results_path"]))
    summary = summarize(rows)
    text = render_summary_table(rows)
    diag = diagnostics(rows)
    assert summary.total_tasks >= 2
    assert "tasks=" in text
    assert "failure_reason_histogram" in diag
    assert "stability" in diag


def test_paired_comparison_and_ci() -> None:
    ci = wilson_interval(5, 10)
    assert ci[0] <= ci[1]
    r = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    a = r.run(Path("benchmark_tasks/villani_bench_v1"), "cmd:python -c 'print(1)'", None, None, None, task_id="bugfix_001_datetime_cli")
    b = r.run(Path("benchmark_tasks/villani_bench_v1"), "cmd:python -c 'print(2)'", None, None, None, task_id="bugfix_001_datetime_cli")
    from villani_code.benchmark.reporting import load_results

    comp = paired_compare(load_results(Path(a["results_path"])), load_results(Path(b["results_path"])))
    assert "delta_ci95" in comp


def test_smoke_load_all_tasks_with_track_filter() -> None:
    tasks = load_tasks(Path("benchmark_tasks/villani_bench_v1"), track="core")
    assert len(tasks) >= 25
    assert {task.family.value for task in tasks} == {"bugfix", "repro_test", "localize_patch", "terminal_workflow"}


def test_healthcheck() -> None:
    health = run_healthcheck(Path("benchmark_tasks/villani_bench_v1"))
    assert health["tasks"] >= 25
    assert "families" in health
