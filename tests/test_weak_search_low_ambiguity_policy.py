from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.policy import RuntimeStrategy, decide_runtime_policy


def test_direct_repair_selected_for_bounded_single_target_even_with_higher_file_ceiling():
    cfg = BenchmarkRuntimeConfig(
        enabled=True,
        expected_files=["src/app.py"],
        max_files_touched=4,
        visible_verification=["pytest -q tests/test_app.py::test_regression"],
        task_family="bugfix",
        task_type="single_file_bugfix",
    )
    decision = decide_runtime_policy(
        benchmark_config=cfg,
        is_interactive=False,
        task_family="bugfix",
        task_type="single_file_bugfix",
        previous_candidate_failed=False,
        no_progress_cycles=0,
        has_stacktrace_or_error=True,
        objective_text="Fix src/app.py regression",
        failure_text="Traceback references src/app.py",
    )
    assert decision.strategy == RuntimeStrategy.DIRECT_REPAIR_FIRST


def test_ambiguous_multifile_task_does_not_use_direct_repair_first():
    cfg = BenchmarkRuntimeConfig(
        enabled=True,
        expected_files=["src/app.py", "src/config.py"],
        max_files_touched=4,
        visible_verification=["pytest -q tests/test_app.py"],
        task_family="bugfix",
        task_type="multi_file",
    )
    decision = decide_runtime_policy(
        benchmark_config=cfg,
        is_interactive=False,
        task_family="bugfix",
        task_type="multi_file",
        previous_candidate_failed=False,
        no_progress_cycles=0,
        has_stacktrace_or_error=False,
    )
    assert decision.strategy != RuntimeStrategy.DIRECT_REPAIR_FIRST


def test_repro_two_stage_task_does_not_use_direct_repair_first():
    cfg = BenchmarkRuntimeConfig(
        enabled=True,
        task_id="repro-case",
        expected_files=["src/app.py"],
        max_files_touched=3,
        visible_verification=["python repro.py"],
        task_family="repro_test",
        task_type="repro",
    )
    decision = decide_runtime_policy(
        benchmark_config=cfg,
        is_interactive=False,
        task_family="repro_test",
        task_type="repro",
        previous_candidate_failed=False,
        no_progress_cycles=0,
        has_stacktrace_or_error=True,
        failure_text="src/app.py",
    )
    assert decision.strategy != RuntimeStrategy.DIRECT_REPAIR_FIRST
