from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutionResult, CandidateExecutor
from villani_code.runtime.controller import WeakSearchController
from villani_code.runtime.policy import WeakSearchPolicyProfile, decide_runtime_policy


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
            task_id="task-direct",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=1,
            visible_verification=["python -m pytest -q tests/test_app.py"],
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None

    def _execute_tool_with_policy(self, *_args, **_kwargs):
        return {"content": "", "is_error": False}


def test_easy_task_uses_direct_repair_profile():
    cfg = BenchmarkRuntimeConfig(enabled=True, expected_files=["src/app.py"], max_files_touched=1, visible_verification=["python -m pytest -q tests/test_app.py"])
    decision = decide_runtime_policy(benchmark_config=cfg, is_interactive=False, task_family="localize_patch", previous_candidate_failed=False, no_progress_cycles=0, has_stacktrace_or_error=False)
    assert decision.profile == WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH


def test_direct_repair_attempt_happens_before_hypothesis_search(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    calls = []

    def fake_eval(self, **kwargs):
        calls.append((kwargs["hypothesis_id"], kwargs["execution_mode"], kwargs["max_candidate_turns"], kwargs["max_candidate_tool_calls"]))
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_exit_codes": [1], "target_command_count": 1},
            attempt_category="verification_failed",
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_eval)
    WeakSearchController(DummyRunner(tmp_path), "fix bug").run()

    assert calls
    assert calls[0][0] == "candidate-0"
    assert calls[0][1] == "direct_repair"
    assert calls[0][2] == 1
    assert calls[0][3] == 4


def test_direct_repair_prompt_is_bounded(tmp_path: Path):
    runner = DummyRunner(tmp_path)
    ex = CandidateExecutor(runner, "fix bug", 12, 1)
    prompt = ex._build_prompt(
        suspect="src/app.py",
        hypothesis_text="repair edge case",
        constraints={"expected_files": ["src/app.py"]},
        failed_attempt_summary=[],
        runtime_profile="benchmark",
        baseline_handle="clean-copy",
        policy_profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH.value,
        execution_mode="direct_repair",
    )
    assert "bounded single-file bugfix" in prompt
    assert "Forbidden: broad repository exploration" in prompt
    assert "edit exactly one file" in prompt
