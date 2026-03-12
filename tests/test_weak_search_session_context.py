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
        self._ensure_calls = 0
        self.benchmark_config = BenchmarkRuntimeConfig(
            enabled=True,
            task_id="task-ctx",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=1,
            visible_verification=["python -m pytest -q tests/test_app.py"],
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        self._ensure_calls += 1

    def _execute_tool_with_policy(self, *_args, **_kwargs):
        return {"content": "", "is_error": False}


def test_session_context_reused_and_planning_not_rebuilt_per_candidate(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    runner = DummyRunner(tmp_path)
    contexts = []

    def fake_eval(self, **kwargs):
        contexts.append(kwargs.get("session_context"))
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_exit_codes": [1], "target_command_count": 1},
            attempt_category="verification_failed",
            session_context_reused=True,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_eval)
    WeakSearchController(runner, "fix bug").run()

    assert runner._ensure_calls == 1
    assert contexts
    assert contexts[0] is not None
    assert contexts[0].planning_initialized is True
