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
            task_id="task-regression",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=1,
            visible_verification=["python -m pytest -q tests/test_app.py"],
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None

    def _execute_tool_with_policy(self, *_args, **_kwargs):
        return {"content": "", "is_error": False}


def test_escalation_is_conditional_not_automatic(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    def fake_eval(self, **kwargs):
        return CandidateExecutionResult(
            hard_fail=True,
            attempt_category="blocked_model_failure",
            blocked_reason="blocked_model_failure",
            attempt_summary="model crashed before touching suspect",
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_eval)
    out = WeakSearchController(DummyRunner(tmp_path), "fix bug").run()
    assert out["weak_search"]["candidate_0_attempted"] is True
    assert out["weak_search"]["escalation_occurred"] is False


def test_timing_telemetry_fields_recorded(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    def fake_eval(self, **kwargs):
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_exit_codes": [1], "target_command_count": 1},
            attempt_category="verification_failed",
            workspace_prep_seconds=0.11,
            model_execution_seconds=0.22,
            verification_seconds=0.33,
            candidate_total_seconds=0.77,
            policy_profile="direct_repair_fast_path",
            direct_repair_attempted=True,
            direct_repair_suspect="src/app.py",
            hypothesis_stage_skipped_initially=True,
            session_context_reused=True,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_eval)
    WeakSearchController(DummyRunner(tmp_path), "fix bug").run()
    run_dir = max((tmp_path / ".villani_code" / "runs").iterdir(), key=lambda p: p.stat().st_mtime)
    board = (run_dir / "blackboard.json").read_text(encoding="utf-8")
    assert "workspace_prep_seconds" in board
    assert "model_execution_seconds" in board
    assert "verification_seconds" in board
    assert "candidate_total_seconds" in board
    assert "policy_profile" in board
    assert "direct_patch_target_file" in board
    assert "session_context_reused" in board
