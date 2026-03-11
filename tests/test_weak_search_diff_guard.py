from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.synthesize.diff_guard import guard_candidate_diff
from villani_code.synthesize.edit_budget import EditBudget


def test_diff_guard_rejects_broad_patch():
    cfg = BenchmarkRuntimeConfig(enabled=True, allowlist_paths=["src/"], forbidden_paths=[".git/"])
    decision = guard_candidate_diff(files_touched=["src/a.py", "src/b.py"], changed_lines=100, hunks=6, budget=EditBudget(max_files=1, max_lines=20), benchmark_config=cfg)
    assert not decision.allowed
