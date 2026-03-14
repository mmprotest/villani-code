from __future__ import annotations

from pathlib import Path

import pytest

from villani_code.benchmark.adapters import ClaudeCodeAdapter, VillaniAdapter
from villani_code.benchmark.health import run_healthcheck
from villani_code.benchmark.models import BenchmarkTrack, FieldQuality, TaskSource, TelemetryQuality
from villani_code.benchmark.reporting import diagnostics, load_results, paired_compare, render_summary_table, summarize
from villani_code.benchmark.runner import BenchmarkRunner
from villani_code.benchmark.stats import wilson_interval
from villani_code.benchmark.task_loader import TaskLoadError, load_task, load_tasks


def test_task_loader_parses_valid_task() -> None:
    task = load_task(Path("benchmark_tasks/villani_bench_v1/bugfix_001_datetime_cli"))
    assert task.id == "bugfix_001_datetime_cli"
    assert task.benchmark_track == BenchmarkTrack.CORE
    assert task.source_type in {TaskSource.CURATED, TaskSource.SEEDED, TaskSource.MUTATED}
    assert len(task.task_checksum or "") > 5
    assert task.allowed_paths == ["src/", "tests/"]
    assert task.expected_touched_max == 3


def test_task_loader_new_optional_fields_default_back_compat() -> None:
    task = load_task(Path("benchmark_tasks/villani_bench_v1/localize_001_feature_flag"))
    assert task.allowed_paths == []
    assert task.forbidden_paths == []
    assert task.expected_touched_max is None
    assert task.inspect_only is False
    assert task.recovery_expected is False
    assert task.adjacency_expected is False
    assert task.hidden_verifier is None
    assert task.task_type is None


def test_feature_flag_name_stays_core() -> None:
    task = load_task(Path("benchmark_tasks/villani_bench_v1/localize_001_feature_flag"))
    assert task.benchmark_track == BenchmarkTrack.CORE


def test_task_loader_requires_explicit_track(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "repo").mkdir()
    (task_dir / "prompt.txt").write_text("fix bug", encoding="utf-8")
    (task_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (task_dir / "task.yaml").write_text(
        "id: x\nfamily: bugfix\ndifficulty: easy\nlanguage: python\nmax_minutes: 1\nmax_files_touched: 1\nexpected_artifacts: [patch]\nvisible_verification: ['true']\nhidden_verification: ['true']\nsuccess_policy: {require_visible_pass: true, require_hidden_pass: true, fail_on_timeout: true, fail_on_repo_dirty_outside_allowlist: true}\nallowlist_paths: ['src/']\n",
        encoding="utf-8",
    )
    with pytest.raises(TaskLoadError):
        load_task(task_dir)


def test_run_emits_honest_telemetry() -> None:
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
    row = rows[0]
    assert row.telemetry_quality in {TelemetryQuality.EXACT, TelemetryQuality.INFERRED, TelemetryQuality.UNAVAILABLE}
    assert row.telemetry_field_quality_map.get("num_shell_commands") in {FieldQuality.EXACT, FieldQuality.INFERRED, FieldQuality.UNAVAILABLE}
    if row.telemetry_field_quality_map.get("num_shell_commands") != FieldQuality.EXACT:
        assert row.num_shell_commands is None


def test_adapter_fairness_declarations() -> None:
    villani = VillaniAdapter()
    claude = ClaudeCodeAdapter()
    assert villani.fairness_classification.value == "approximately_comparable"
    assert claude.fairness_classification.value == "coarse_wrapper_only"
    assert "telemetry richness still differs" in villani.fairness_notes
    assert "coarse CLI wrapper" in claude.fairness_notes


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
    assert "same_model_comparison" in text
    assert "by_fairness_class" in diag
    assert "small_sample_warning" in diag


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


def test_healthcheck_expanded() -> None:
    health = run_healthcheck(Path("benchmark_tasks/villani_bench_v1"))
    assert health["tasks"] >= 25
    assert "errors" in health
    assert health["ok"]


def test_new_runtime_stressing_tasks_load() -> None:
    for task_id in ["hidden_multi_file_bug", "false_fix_trap", "two_stage_fix"]:
        task = load_task(Path(f"benchmark_tasks/villani_bench_v1/{task_id}"))
        assert task.metadata.benchmark_bucket == "runtime_stressing"
        assert task.metadata.runtime_stressors


def test_reporting_exposes_same_model_comparison() -> None:
    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark-test"))
    data = runner.run(
        suite_dir=Path("benchmark_tasks/villani_bench_v1"),
        task_id="terminal_001_python_module_entry",
        agent="cmd:python -c 'from pathlib import Path; Path(\"app/__main__.py\").write_text(\"print(1)\\n\", encoding=\"utf-8\")'",
        model="tiny-model",
        base_url=None,
        api_key=None,
    )
    rows = load_results(Path(data["results_path"]))
    table = render_summary_table(rows)
    assert "same_model_comparison" in table
    assert rows[0].pass_rate in {0.0, 1.0}
    assert rows[0].failed in {0, 1}


def test_new_tasks_fail_before_fix() -> None:
    import subprocess

    for task_id, test_file in [
        ("hidden_multi_file_bug", "tests/test_pagination.py"),
        ("false_fix_trap", "tests/test_config_loading.py"),
        ("two_stage_fix", "tests/test_cache.py"),
    ]:
        repo = Path(f"benchmark_tasks/villani_bench_v1/{task_id}/repo")
        proc = subprocess.run(["pytest", "-q", test_file], cwd=repo, capture_output=True, text=True)
        assert proc.returncode != 0


def test_runner_surfaces_agent_startup_stderr_snippet(monkeypatch) -> None:
    from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
    from villani_code.benchmark.models import FairnessClassification, FailureReason, FieldQuality, TelemetryQuality

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
                stderr='usage: villani_code.cli run ...\nerror: unrecognized arguments: --emit-runtime-events\n',
                exit_code=2,
                timeout=False,
                runtime_seconds=0.05,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={'num_shell_commands': FieldQuality.INFERRED},
                events=[AdapterEvent(type='command_started', timestamp=1.0, payload={})],
            )

    monkeypatch.setattr('villani_code.benchmark.runner.build_agent_runner', lambda agent: FakeRunner())

    runner = BenchmarkRunner(output_dir=Path('artifacts/benchmark-test'))
    data = runner.run(
        suite_dir=Path('benchmark_tasks/villani_bench_v1'),
        task_id='terminal_001_python_module_entry',
        agent='villani',
        model='tiny-model',
        base_url=None,
        api_key=None,
    )
    rows = load_results(Path(data['results_path']))
    row = rows[0]
    assert row.success == 0
    assert row.failure_reason == FailureReason.AGENT_CRASH
    assert row.error is not None
    assert 'exited with code 2' in row.error
    assert 'stderr:' in row.error
    assert '--emit-runtime-events' in row.error


def test_hidden_verifier_alias_normalized_to_hidden_verification(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    (task_dir / "repo").mkdir(parents=True)
    (task_dir / "prompt.txt").write_text("fix bug", encoding="utf-8")
    (task_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (task_dir / "task.yaml").write_text(
        "id: x\nbenchmark_track: core\nfamily: bugfix\ndifficulty: easy\nlanguage: python\nmax_minutes: 1\nmax_files_touched: 1\nexpected_artifacts: [patch]\nvisible_verification: ['true']\nhidden_verifier: ['python -m pytest tests/test_x.py -q']\nhidden_verification: ['python -m pytest tests/test_x.py -q']\nsuccess_policy: {require_visible_pass: true, require_hidden_pass: true, fail_on_timeout: true, fail_on_repo_dirty_outside_allowlist: true}\nallowlist_paths: ['src/']\n",
        encoding="utf-8",
    )
    task = load_task(task_dir)
    assert task.hidden_verification == ["python -m pytest tests/test_x.py -q"]
    assert task.hidden_verifier is None


def test_terminal_001_is_not_inspect_only() -> None:
    task = load_task(Path("benchmark_tasks/villani_bench_v1/terminal_001_python_module_entry"))
    assert task.inspect_only is False


def test_bugfix_005_retry_threshold_fixture_matches_seeded_bug() -> None:
    import json

    task_dir = Path("benchmark_tasks/villani_bench_v1/bugfix_005_retry_threshold")
    task = load_task(task_dir)
    assert task.id == "bugfix_005_retry_threshold"

    pyproject = (task_dir / "repo" / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.pytest.ini_options]" in pyproject
    assert 'pythonpath = ["src"]' in pyproject

    prompt = (task_dir / "prompt.txt").read_text(encoding="utf-8").lower()
    assert "500" in prompt
    assert "retry" in prompt
    assert "400" in prompt

    metadata = json.loads((task_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["name"] == "bugfix_005_retry_threshold"
    assert "500" in metadata["description"]


def test_suite_loads_renamed_bugfix_005_task() -> None:
    tasks = load_tasks(Path("benchmark_tasks/villani_bench_v1"))
    task_ids = {task.id for task in tasks}
    assert "bugfix_005_retry_threshold" in task_ids
    assert "bugfix_005_cache_key_args" not in task_ids
