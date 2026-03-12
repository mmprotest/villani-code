from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutor, WeakSearchSessionContext


class GuardClient:
    def create_message(self, _payload, stream=False):
        return {
            "content": [
                {
                    "type": "tool_use",
                    "id": "u1",
                    "name": "Ls",
                    "input": {"path": "."},
                }
            ]
        }


class DummyRunner:
    def __init__(self, repo: Path):
        self.repo = repo
        self.client = GuardClient()
        self.model = "m"
        self.max_tokens = 128
        self.benchmark_config = BenchmarkRuntimeConfig(enabled=True, expected_files=["src/app.py"], visible_verification=["python -c \"print(1)\""])

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None

    def _execute_tool_with_policy(self, *_args, **_kwargs):
        return {"content": "", "is_error": False}


def test_direct_mode_blocks_broad_exploration_before_target_inspection(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    ex = CandidateExecutor(DummyRunner(tmp_path), "fix", 12, 1)
    result = ex.evaluate_candidate(
        repo_path=tmp_path,
        objective="fix",
        suspect_region="src/app.py",
        hypothesis_id="candidate-0",
        hypothesis="repair local bug",
        constraints={"visible_verification": ["python -c \"print(1)\""]},
        runtime_profile="benchmark",
        benchmark_config=BenchmarkRuntimeConfig(enabled=True, expected_files=["src/app.py"], visible_verification=["python -c \"print(1)\""]),
        baseline_handle="clean-copy",
        edit_budget=ex.edit_budget,
        branch_failure_history=[],
        timeout_budget_seconds=30.0,
        attempt_id="att-1",
        max_candidate_turns=2,
        max_candidate_tool_calls=4,
        execution_mode="direct_repair",
        session_context=WeakSearchSessionContext(planning_prompt="fix"),
    )
    assert result.blocked_reason == "blocked_model_failure"
    assert result.attempt_summary.startswith("direct_repair_thrash:first_tool_not_target_inspection")
    assert result.exploration_block_triggered is True
