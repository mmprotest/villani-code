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
        calls.append((kwargs.get("target_file"), "direct_patch"))
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_exit_codes": [1], "target_command_count": 1},
            attempt_category="verification_failed",
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch", fake_eval)
    WeakSearchController(DummyRunner(tmp_path), "fix bug").run()

    assert calls
    assert calls[0][0] == "src/app.py"
    assert calls[0][1] == "direct_patch"


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
    assert "bounded local repair" in prompt
    assert "Exact implementation target file: src/app.py" in prompt
    assert "Broad exploration is not allowed" in prompt


def test_direct_repair_uses_expected_file_and_skips_hypothesis_stage(monkeypatch, tmp_path: Path):
    (tmp_path / "src" / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app" / "config.py").write_text("SETTING=1\n", encoding="utf-8")

    runner = DummyRunner(tmp_path)
    runner.benchmark_config.expected_files = ["src/app/config.py"]
    runner.benchmark_config.allowlist_paths = ["tests/", "src/"]

    called = {"hyp": 0, "suspect": ""}

    def fake_generate(*_args, **_kwargs):
        called["hyp"] += 1
        raise AssertionError("hypothesis generation should be skipped on initial direct attempt")

    def fake_eval(self, **kwargs):
        called["suspect"] = kwargs["target_file"]
        return CandidateExecutionResult(
            changed_files=["src/app/config.py"],
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_exit_codes": [0], "target_command_count": 1, "target_verification_passed": True, "static_sanity_passed": True},
            attempt_category="candidate_verified",
            success=True,
            diff_text="diff --git a/src/app/config.py b/src/app/config.py\n",
            hypothesis_stage_skipped_initially=True,
            policy_profile="direct_repair_fast_path",
            direct_repair_attempted=True,
            direct_repair_suspect="src/app/config.py",
        )

    monkeypatch.setattr("villani_code.hypothesize.generator.generate_hypotheses", fake_generate)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch", fake_eval)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.commit_candidate", lambda self, repo_path, candidate_result: None)

    out = WeakSearchController(runner, "fix config precedence").run()
    assert out["weak_search"]["direct_patch_attempted"] is True
    assert out["weak_search"]["strategy_selected"] == "direct_repair_first"
    assert called["suspect"] == "src/app/config.py"
    assert called["hyp"] == 0


def test_direct_repair_prompt_profile_is_compact_and_targeted(tmp_path: Path):
    runner = DummyRunner(tmp_path)
    ex = CandidateExecutor(runner, "fix bug", 12, 1)
    prompt = ex._build_prompt(
        suspect="src/app/config.py",
        hypothesis_text="repair precedence",
        constraints={"expected_files": ["src/app/config.py"]},
        failed_attempt_summary=[],
        runtime_profile="benchmark",
        baseline_handle="clean-copy",
        policy_profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH.value,
        execution_mode="direct_repair",
    )
    assert "bounded local repair" in prompt
    assert "Inspect the target implementation file first" in prompt
    assert "smallest valid patch" in prompt


def test_direct_repair_prompt_is_materially_smaller_than_general_prompt(tmp_path: Path):
    runner = DummyRunner(tmp_path)
    ex = CandidateExecutor(runner, "fix bug", 12, 1)
    direct_prompt = ex._build_prompt(
        suspect="src/app.py",
        hypothesis_text="repair edge case",
        constraints={"expected_files": ["src/app.py"], "visible_verification": ["pytest -q tests/test_app.py::test_fast"]},
        failed_attempt_summary=[],
        runtime_profile="benchmark",
        baseline_handle="clean-copy",
        policy_profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH.value,
        execution_mode="direct_repair",
    )
    heavy_prompt = ex._build_prompt(
        suspect="src/app.py",
        hypothesis_text="repair edge case",
        constraints={"expected_files": ["src/app.py"], "visible_verification": ["pytest -q tests/test_app.py::test_fast"], "allowlist_paths": ["src/"]},
        failed_attempt_summary=["a", "b", "c"],
        runtime_profile="benchmark",
        baseline_handle="clean-copy",
        policy_profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH.value,
        execution_mode="heavy",
    )
    assert len(direct_prompt) < len(heavy_prompt)
