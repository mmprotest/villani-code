from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutor


class DummyRunner:
    def __init__(self, repo: Path, response: dict):
        self.repo = repo
        self.client = type("C", (), {"create_message": lambda _self, _payload, stream=False: response})()
        self.model = "m"
        self.max_tokens = 256
        self.event_callback = lambda _e: None
        self.benchmark_config = BenchmarkRuntimeConfig(enabled=True, visible_verification=[])


def _make_executor(tmp_path: Path, response: dict) -> CandidateExecutor:
    return CandidateExecutor(DummyRunner(tmp_path, response), "fix bug", 100, 1)


def test_stage1_contract_failure_when_no_tool_call(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    ex = _make_executor(tmp_path, {"content": [{"type": "text", "text": "here is a patch as prose"}]})
    out = ex.evaluate_direct_patch(
        repo_path=tmp_path,
        objective="fix",
        target_file="src/app.py",
        target_file_contents="x=1\n",
        verification_target="",
        constraints={},
        benchmark_config=ex.runner.benchmark_config,
        attempt_id="a1",
        timeout_budget_seconds=30.0,
    )
    assert out.attempt_category == "proposal_contract_failure"
    assert out.proposal_contract_failure_reason == "no_tool_call_returned"


def test_stage1_full_file_tool_call_success(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    response = {
        "content": [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "propose_full_file_rewrite",
                "input": {"file_path": "src/app.py", "new_content": "x=2\n", "rationale": "fix"},
            }
        ]
    }
    ex = _make_executor(tmp_path, response)
    out = ex.evaluate_direct_patch(
        repo_path=tmp_path,
        objective="fix",
        target_file="src/app.py",
        target_file_contents="x=1\n",
        verification_target="",
        constraints={},
        benchmark_config=ex.runner.benchmark_config,
        attempt_id="a2",
        timeout_budget_seconds=30.0,
    )
    assert out.apply_mode == "full_file"
    assert out.meaningful_patch_produced is True


def test_stage1_snippet_tool_call_success(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    response = {
        "content": [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "propose_snippet_replace",
                "input": {"file_path": "src/app.py", "old_snippet": "x=1\n", "new_snippet": "x=2\n"},
            }
        ]
    }
    ex = _make_executor(tmp_path, response)
    out = ex.evaluate_direct_patch(
        repo_path=tmp_path,
        objective="fix",
        target_file="src/app.py",
        target_file_contents="x=1\n",
        verification_target="",
        constraints={},
        benchmark_config=ex.runner.benchmark_config,
        attempt_id="a3",
        timeout_budget_seconds=30.0,
    )
    assert out.apply_mode == "snippet_replace"
    assert out.meaningful_patch_produced is True
