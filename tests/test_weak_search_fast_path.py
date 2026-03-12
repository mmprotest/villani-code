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
            task_id="task-1",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=1,
            visible_verification=["python -m pytest -q tests/test_app.py"],
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None

    def _execute_tool_with_policy(self, *_args, **_kwargs):
        return {"content": "", "is_error": False}


def test_candidate0_attempted_before_hypothesis_fanout(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    calls: list[str] = []

    def fake_eval(self, **kwargs):
        calls.append(kwargs["hypothesis_id"])
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_exit_codes": [1], "target_command_count": 1},
            attempt_category="verification_failed",
            score=0.1,
            workspace_prep_seconds=0.01,
            model_execution_seconds=0.01,
            verification_seconds=0.01,
            candidate_total_seconds=0.03,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_eval)
    out = WeakSearchController(DummyRunner(tmp_path), "fix bug").run()
    assert calls[0] == "candidate-0"
    assert out["weak_search"]["candidate_0_attempted"] is True


def test_timing_metrics_recorded_on_attempt(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    def fake_eval(self, **kwargs):
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_exit_codes": [0], "target_command_count": 1},
            attempt_category="candidate_verified",
            success=True,
            score=1.0,
            diff_text="--- a/src/app.py\n+++ b/src/app.py\n",
            workspace_prep_seconds=0.2,
            prompt_build_seconds=0.1,
            model_execution_seconds=0.4,
            tool_execution_seconds=0.2,
            verification_seconds=0.3,
            candidate_total_seconds=1.2,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_eval)
    WeakSearchController(DummyRunner(tmp_path), "fix bug").run()
    run_dir = max((tmp_path / ".villani_code" / "runs").iterdir(), key=lambda p: p.stat().st_mtime)
    board = (run_dir / "blackboard.json").read_text(encoding="utf-8")
    assert "workspace_prep_seconds" in board
    assert "candidate_total_seconds" in board
