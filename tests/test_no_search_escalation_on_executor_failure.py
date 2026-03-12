from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutionResult
from villani_code.runtime.controller import WeakSearchController


class DummyClient:
    def create_message(self, _payload, stream=False):
        return {"content": []}


class DummyRunner:
    def __init__(self, repo: Path):
        self.repo = repo
        self.client = DummyClient()
        self.model = "m"
        self.max_tokens = 128
        self.event_callback = lambda _e: None
        self.benchmark_config = BenchmarkRuntimeConfig(
            enabled=True,
            task_id="task-no-escalate",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=2,
            visible_verification=["pytest -q tests/test_app.py::test_fast"],
            task_family="bugfix",
            task_type="single_file_bugfix",
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None


def test_executor_failures_do_not_escalate_to_stage3(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    monkeypatch.setattr(
        "villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch",
        lambda *_args, **_kwargs: CandidateExecutionResult(
            hard_fail=True,
            blocked_reason="blocked_runtime_error",
            attempt_category="blocked_runtime_error",
        ),
    )
    monkeypatch.setattr(
        "villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_guided_retry",
        lambda *_args, **_kwargs: CandidateExecutionResult(
            hard_fail=True,
            blocked_reason="blocked_runtime_error",
            attempt_category="blocked_runtime_error",
        ),
    )

    out = WeakSearchController(DummyRunner(tmp_path), "fix bug in src/app.py").run()
    assert out["weak_search"]["strategy_stage_used"] != "search_runtime"
    assert out["weak_search"]["stage3_search_used"] is False
    assert out["weak_search"]["search_escalation_blocked_due_to_executor_failure"] is True


def test_meaningful_verification_failure_can_escalate(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    monkeypatch.setattr(
        "villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch",
        lambda *_args, **_kwargs: CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_text="--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x=1\n+x=2",
            diff_stats={"changed_line_count": 1},
            attempt_category="verification_failed",
            meaningful_patch_produced=True,
            score=0.5,
        ),
    )
    monkeypatch.setattr(
        "villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_guided_retry",
        lambda *_args, **_kwargs: CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_text="--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x=1\n+x=3",
            diff_stats={"changed_line_count": 1},
            attempt_category="verification_failed",
            meaningful_patch_produced=True,
            score=0.6,
        ),
    )

    out = WeakSearchController(DummyRunner(tmp_path), "fix bug in src/app.py").run()
    assert out["weak_search"]["stage3_search_used"] is True
