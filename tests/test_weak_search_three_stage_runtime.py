from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutionResult, CandidateExecutor
from villani_code.runtime.controller import WeakSearchController
from villani_code.runtime.policy import AmbiguityLevel, RuntimeStrategy, classify_task_ambiguity, decide_runtime_policy


class DummyClient:
    def __init__(self, text: str = ""):
        self.text = text

    def create_message(self, _payload, stream=False):
        return {"content": [{"type": "text", "text": self.text}]}


class DummyRunner:
    def __init__(self, repo: Path, cfg: BenchmarkRuntimeConfig, text: str = ""):
        self.repo = repo
        self.client = DummyClient(text)
        self.model = "m"
        self.max_tokens = 128
        self.event_callback = lambda _e: None
        self.benchmark_config = cfg

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None


def test_ambiguity_classifier_prefers_low_for_single_impl_even_when_file_budget_is_large():
    cfg = BenchmarkRuntimeConfig(
        enabled=True,
        expected_files=["src/app.py"],
        max_files_touched=5,
        visible_verification=["pytest -q tests/test_app.py::test_regression"],
    )
    level, reasons = classify_task_ambiguity(
        benchmark_config=cfg,
        is_interactive=False,
        task_family="bugfix",
        task_type="single_file_bugfix",
        has_stacktrace_or_error=True,
        objective_text="Fix src/app.py",
        failure_text="Traceback in src/app.py",
    )
    assert level == AmbiguityLevel.LOW
    assert reasons


def test_direct_patch_prompt_prefers_full_file_then_snippet_then_diff(tmp_path: Path):
    cfg = BenchmarkRuntimeConfig(enabled=True, expected_files=["src/app.py"], visible_verification=["pytest -q tests/test_app.py::test_regression"])
    runner = DummyRunner(tmp_path, cfg)
    ex = CandidateExecutor(runner, "fix bug", 12, 1)
    prompt = ex._build_direct_transform_prompt(
        objective="fix bug",
        target_file="src/app.py",
        target_file_contents="x = 1\n",
        failing_test_file="tests/test_app.py",
        failing_test_contents="def test_regression():\n    assert False\n",
        verification_target="pytest -q tests/test_app.py::test_regression",
    )
    assert "Return one format only" in prompt
    assert "Full replacement example:" in prompt
    assert "Snippet replacement example:" in prompt
    assert "Unified diff example:" in prompt
    assert "--- FILE: src/app.py ---" in prompt


def test_stage1_failure_escalates_to_stage2_before_stage3(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    cfg = BenchmarkRuntimeConfig(
        enabled=True,
        expected_files=["src/app.py"],
        allowlist_paths=["src/"],
        visible_verification=["pytest -q tests/test_app.py::test_regression"],
        task_family="bugfix",
        task_type="single_file_bugfix",
    )
    runner = DummyRunner(tmp_path, cfg)
    calls: list[str] = []

    def fake_direct(self, **kwargs):
        calls.append("stage1")
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_text="diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n",
            attempt_category="verification_failed",
            score=0.2,
        )

    def fake_guided(self, **kwargs):
        calls.append("stage2")
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_text="diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n",
            attempt_category="blocked_model_failure",
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch", fake_direct)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_guided_retry", fake_guided)

    out = WeakSearchController(runner, "fix src/app.py regression").run()
    assert calls[:2] == ["stage1", "stage2"]
    assert out["weak_search"]["stop_reason"] in {"no_progress", "exhausted_budget", "solved", "blocked"}


def test_policy_uses_medium_as_guided_retry_default():
    cfg = BenchmarkRuntimeConfig(
        enabled=True,
        expected_files=["src/a.py", "src/b.py"],
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
        objective_text="fix bug",
        failure_text="",
    )
    assert decision.strategy in {RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE, RuntimeStrategy.FULL_WEAK_SEARCH}
