from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutionResult
from villani_code.runtime.controller import WeakSearchController
from villani_code.runtime.policy import AmbiguityLevel, classify_task_ambiguity


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
            task_id="task-bugfix-target",
            allowlist_paths=["src/main.py", "src/"],
            expected_files=[],
            max_files_touched=2,
            visible_verification=["pytest -q tests/test_main.py::test_bug"],
            task_family="bugfix",
            task_type="single_file_bugfix",
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None


def test_obvious_bugfix_not_high_ambiguity_when_target_plausible():
    cfg = BenchmarkRuntimeConfig(
        enabled=True,
        task_id="task-bug",
        allowlist_paths=["src/app.py"],
        expected_files=[],
        max_files_touched=2,
        visible_verification=["pytest -q tests/test_app.py::test_bug"],
        task_family="bugfix",
        task_type="single_file_bugfix",
    )
    level, reasons = classify_task_ambiguity(
        benchmark_config=cfg,
        is_interactive=False,
        task_family="bugfix",
        task_type="single_file_bugfix",
        has_stacktrace_or_error=True,
        objective_text="fix bug in parser",
        failure_text="AssertionError in parser",
    )
    assert level in {AmbiguityLevel.LOW, AmbiguityLevel.MEDIUM}
    assert "multiple_plausible_implementation_files" not in reasons


def test_bugfix_target_selection_not_blank(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.py").write_text("x=1\n", encoding="utf-8")

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

    out = WeakSearchController(DummyRunner(tmp_path), "fix bug where parser returns wrong value").run()
    assert out["weak_search"]["target_file"]
    assert out["weak_search"]["target_selection_reason"]
    assert out["weak_search"]["target_selection_confidence"]
