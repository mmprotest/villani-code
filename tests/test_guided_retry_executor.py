from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutionResult, CandidateExecutor
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
            task_id="task-guided",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=1,
            visible_verification=["pytest -q tests/test_app.py::test_fast"],
            task_family="bugfix",
            task_type="single_file_bugfix",
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None


def test_guided_retry_prompt_differs_from_stage1(monkeypatch, tmp_path: Path):
    ex = CandidateExecutor(DummyRunner(tmp_path), "fix bug", 20, 1)
    prompt1 = ex._build_direct_transform_prompt(
        objective="fix bug",
        target_file="src/app.py",
        target_file_contents="x=1\n",
        failing_test_file="tests/test_app.py",
        failing_test_contents="def test_fast():\n    assert False\n",
        verification_target="pytest -q tests/test_app.py::test_fast",
        stage_name="stage1",
        retry_hint="",
    )
    prompt2 = ex._build_direct_transform_prompt(
        objective="fix bug",
        target_file="src/app.py",
        target_file_contents="x=1\n",
        failing_test_file="",
        failing_test_contents="",
        verification_target="pytest -q tests/test_app.py::test_fast",
        stage_name="stage2",
        retry_hint="Stage1 failure type: unusable_output_format. previous patch format invalid; return whole file",
    )
    assert "Stage: stage1" in prompt1
    assert "Stage: stage2" in prompt2
    assert "Retry guidance:" in prompt2
    assert "SUPPORTING FAILING TEST" not in prompt2
    assert "Stage1 failure type:" in prompt2


def test_stage2_can_recover_from_stage1_format_failure(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    def fake_direct(self, **kwargs):
        return CandidateExecutionResult(
            hard_fail=True,
            blocked_reason="blocked_runtime_error",
            attempt_category="blocked_runtime_error",
            apply_mode="none",
            apply_failure_reason="unrecognized_patch_format",
        )

    def fake_guided(self, **kwargs):
        assert kwargs["retry_hint"]
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_text="--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x=1\n+x=2",
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_verification_passed": True, "static_sanity_passed": True},
            attempt_category="candidate_verified",
            success=True,
            apply_mode="full_file",
            meaningful_patch_produced=True,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch", fake_direct)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_guided_retry", fake_guided)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.commit_candidate", lambda *_args, **_kwargs: None)

    out = WeakSearchController(DummyRunner(tmp_path), "fix bug in src/app.py").run()
    assert out["weak_search"]["stage1_result"] == "blocked_runtime_error"
    assert out["weak_search"]["stage2_result"] == "candidate_verified"
