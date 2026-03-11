from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.cli import _build_runner
from villani_code.runtime.candidate_executor import CandidateExecutionResult
from villani_code.runtime.controller import WeakSearchController
from villani_code.state import Runner


class DummyClient:
    def create_message(self, _payload, stream=False):
        return {"content": []}


def test_default_runtime_is_weak_search(tmp_path: Path):
    runner = _build_runner(
        base_url="http://example.test",
        model="m",
        repo=tmp_path,
        max_tokens=256,
        stream=False,
        thinking=None,
        unsafe=False,
        verbose=False,
        extra_json=None,
        redact=False,
        dangerously_skip_permissions=False,
        auto_accept_edits=False,
        plan_mode="auto",
        max_repair_attempts=1,
        small_model=False,
        provider="openai",
        api_key="k",
    )
    assert runner.runtime == "weak-search"


def test_runner_uses_weak_search_for_interactive_and_benchmark(tmp_path: Path):
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, runtime="weak-search")
    out = runner.run("inspect")
    assert "weak_search" in out

    bench_runner = Runner(
        client=DummyClient(),
        repo=tmp_path,
        model="m",
        stream=False,
        runtime="weak-search",
        benchmark_config=BenchmarkRuntimeConfig(enabled=True, allowlist_paths=["src/"], expected_files=["src/app.py"]),
    )
    out2 = bench_runner.run("fix")
    assert "weak_search" in out2


def test_controller_calls_shared_candidate_executor(monkeypatch, tmp_path: Path):
    calls: list[dict[str, str]] = []

    def fake_evaluate(self, **kwargs):
        calls.append(kwargs)
        return CandidateExecutionResult(changed_files=["src/app.py"], diff_stats={"changed_line_count":3}, success=False, score=0.2, verification_outputs={"commands": []}, failure_signature="sig", attempt_category="verification_failed")

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_evaluate)

    class DummyRunner:
        def __init__(self):
            self.repo = tmp_path
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
            self.benchmark_config = BenchmarkRuntimeConfig(enabled=True, task_id="repro_case", allowlist_paths=["src/"], expected_files=["src/app.py"], visible_verification=["python -m pytest -q"])
            self.event_callback = lambda _e: None

    result = WeakSearchController(DummyRunner(), "fix bug", timeout_seconds=60).run()
    assert calls
    assert result["weak_search"]["candidate_patches_generated"] >= 1


def test_noop_runtime_emits_blocked_reason(tmp_path: Path):
    class DummyRunner:
        def __init__(self):
            self.repo = tmp_path
            self.benchmark_config = BenchmarkRuntimeConfig(enabled=True)
            self.event_callback = lambda _e: None

    out = WeakSearchController(DummyRunner(), "fix nothing", timeout_seconds=5).run()
    assert out["weak_search"]["candidate_patches_generated"] == 0
    assert out["weak_search"]["stop_reason"] in {"blocked", "no_progress", "timeout_imminent"}
