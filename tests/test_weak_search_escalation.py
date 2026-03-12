from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutionResult
from villani_code.runtime.controller import WeakSearchController


class DummyClient:
    def create_message(self, _payload, stream=False):
        return {"content": []}


class DummyRunner:
    def __init__(self, repo: Path, *, enabled: bool = True):
        self.repo = repo
        self.client = DummyClient()
        self.model = "m"
        self.max_tokens = 128
        self.event_callback = lambda _e: None
        self.benchmark_config = BenchmarkRuntimeConfig(
            enabled=enabled,
            task_id="task-escalation",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=1,
            visible_verification=["pytest -q tests/test_app.py::test_fast"],
            task_family="bugfix",
            task_type="single_file_bugfix",
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None


def test_escalates_to_guided_search_only_after_failed_direct_attempt(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    calls = []

    def fake_direct(self, **kwargs):
        calls.append(("stage1", "direct_patch"))
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_text="diff --git a/src/app.py b/src/app.py\n",
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_verification_passed": False},
            attempt_category="verification_failed",
            score=0.4,
        )

    def fake_guided(self, **kwargs):
        calls.append(("stage2", "guided_retry"))
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_text="diff --git a/src/app.py b/src/app.py\n",
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_verification_passed": True, "static_sanity_passed": True},
            attempt_category="candidate_verified",
            success=True,
            score=0.8,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch", fake_direct)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_guided_retry", fake_guided)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.commit_candidate", lambda self, repo_path, candidate_result: None)

    out = WeakSearchController(DummyRunner(tmp_path), "fix config precedence").run()
    assert calls[0] == ("stage1", "direct_patch")
    assert calls[1] == ("stage2", "guided_retry")
    assert out["weak_search"]["escalated_after_direct_failure"] is True
    assert out["weak_search"]["escalation_reason"] == "partial_fix"
    assert out["weak_search"]["direct_attempt_result"] == "verification_failed"


def test_interactive_and_benchmark_share_strategy_telemetry(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    def fake_eval(self, **kwargs):
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_text="diff --git a/src/app.py b/src/app.py\n",
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_verification_passed": True, "static_sanity_passed": True},
            attempt_category="candidate_verified",
            success=True,
            score=0.9,
            prompt_tokens_first_attempt=42,
            tool_calls_first_attempt=2,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch", fake_eval)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.commit_candidate", lambda self, repo_path, candidate_result: None)

    bench_out = WeakSearchController(DummyRunner(tmp_path, enabled=True), "fix bug").run()
    interactive_out = WeakSearchController(DummyRunner(tmp_path, enabled=False), "fix bug\nTraceback: src/app.py").run()

    assert "strategy_selected" in bench_out["weak_search"]
    assert "strategy_selected" in interactive_out["weak_search"]
    assert "prompt_tokens_first_attempt" in bench_out["weak_search"]
    assert "tool_calls_first_attempt" in interactive_out["weak_search"]
