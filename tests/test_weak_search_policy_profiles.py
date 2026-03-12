from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.budgets import select_runtime_budgets
from villani_code.runtime.policy import WeakSearchPolicyProfile, decide_runtime_policy


def test_easy_benchmark_chooses_fast_path_profile():
    cfg = BenchmarkRuntimeConfig(enabled=True, expected_files=["src/app.py"], max_files_touched=1, visible_verification=["python -m pytest -q tests/test_app.py"])
    decision = decide_runtime_policy(benchmark_config=cfg, is_interactive=False, task_family="localize_patch", previous_candidate_failed=False, no_progress_cycles=0, has_stacktrace_or_error=False)
    assert decision.profile == WeakSearchPolicyProfile.FAST_PATH_SINGLE_FILE


def test_fast_path_budgets_are_reduced():
    cfg = BenchmarkRuntimeConfig(enabled=True, expected_files=["src/app.py"], max_files_touched=1)
    budgets = select_runtime_budgets(cfg, policy_profile=WeakSearchPolicyProfile.FAST_PATH_SINGLE_FILE)
    assert budgets.max_cycles == 2
    assert budgets.max_hypotheses_per_suspect == 2
    assert budgets.max_candidates_per_hypothesis == 1
    assert budgets.max_candidate_turns == 3
    assert budgets.max_candidate_tool_calls == 8


def test_fast_path_failure_escalates_profile():
    cfg = BenchmarkRuntimeConfig(enabled=True, expected_files=["src/app.py"], max_files_touched=1)
    decision = decide_runtime_policy(benchmark_config=cfg, is_interactive=False, task_family="localize_patch", previous_candidate_failed=True, no_progress_cycles=1, has_stacktrace_or_error=True)
    assert decision.profile == WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH
