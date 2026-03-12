from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutor


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
            task_id="task-fallbacks",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=1,
            visible_verification=["pytest -q tests/test_app.py::test_fast"],
        )


def _run_direct(ex: CandidateExecutor, repo: Path):
    return ex.evaluate_direct_patch(
        repo_path=repo,
        objective="fix bug",
        target_file="src/app.py",
        target_file_contents=(repo / "src" / "app.py").read_text(encoding="utf-8"),
        verification_target="pytest -q tests/test_app.py::test_fast",
        constraints={},
        benchmark_config=ex.runner.benchmark_config,
        attempt_id="att-1",
        timeout_budget_seconds=30,
    )


def test_stage1_applies_valid_unified_diff(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    ex = CandidateExecutor(DummyRunner(tmp_path), "fix bug", 20, 1)

    monkeypatch.setattr(ex, "_request_patch_output", lambda *_args, **_kwargs: "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x=1\n+x=2")
    monkeypatch.setattr(ex, "_run_verification", lambda *_args, **_kwargs: ({"target_verification_passed": True, "static_sanity_passed": True, "target_exit_codes": [0], "target_command_count": 1}, True, 0.9, {"minimality": 1.0, "novelty": 1.0}))

    result = _run_direct(ex, tmp_path)
    assert result.success is True
    assert result.apply_mode == "unified_diff"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "x=1\n"


def test_stage1_applies_whole_file_fallback(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    ex = CandidateExecutor(DummyRunner(tmp_path), "fix bug", 20, 1)

    monkeypatch.setattr(ex, "_request_patch_output", lambda *_args, **_kwargs: "NEW FILE CONTENT src/app.py\nx=2\n")
    monkeypatch.setattr(ex, "_run_verification", lambda *_args, **_kwargs: ({"target_verification_passed": True, "static_sanity_passed": True, "target_exit_codes": [0], "target_command_count": 1}, True, 0.9, {"minimality": 1.0, "novelty": 1.0}))

    result = _run_direct(ex, tmp_path)
    assert result.success is True
    assert result.apply_mode == "whole_file"


def test_stage1_applies_snippet_replace_fallback(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    ex = CandidateExecutor(DummyRunner(tmp_path), "fix bug", 20, 1)

    patch = "SNIPPET_REPLACE\nFILE: src/app.py\nOLD_SNIPPET:\nx=1\nNEW_SNIPPET:\nx=2\n"
    monkeypatch.setattr(ex, "_request_patch_output", lambda *_args, **_kwargs: patch)
    monkeypatch.setattr(ex, "_run_verification", lambda *_args, **_kwargs: ({"target_verification_passed": True, "static_sanity_passed": True, "target_exit_codes": [0], "target_command_count": 1}, True, 0.9, {"minimality": 1.0, "novelty": 1.0}))

    result = _run_direct(ex, tmp_path)
    assert result.success is True
    assert result.apply_mode == "snippet_replace"


def test_stage1_malformed_patch_returns_blocked_runtime_error(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    ex = CandidateExecutor(DummyRunner(tmp_path), "fix bug", 20, 1)

    monkeypatch.setattr(ex, "_request_patch_output", lambda *_args, **_kwargs: "--- a/src/other.py\n+++ b/src/other.py\n@@ -1 +1 @@\n-a\n+b")

    result = _run_direct(ex, tmp_path)
    assert result.attempt_category == "blocked_runtime_error"
    assert result.apply_mode == "none"
