from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.cli import _build_runner
from villani_code.hypothesize.generator import generate_hypotheses
from villani_code.runtime.candidate_executor import CandidateExecutionResult, CandidateExecutor
from villani_code.runtime.controller import WeakSearchController
from villani_code.runtime.schemas import SuspectRegion
from villani_code.state import Runner
from villani_code.synthesize.patch_generator import generate_patch_candidates


class DummyClient:
    def __init__(self, payload=None):
        self.payload = payload or {"content": []}

    def create_message(self, _payload, stream=False):
        return self.payload


class DummyRunner:
    def __init__(self, repo: Path, bench: BenchmarkRuntimeConfig | None = None):
        self.repo = repo
        self.benchmark_config = bench or BenchmarkRuntimeConfig(enabled=True, allowlist_paths=["src/"], expected_files=["src/app.py"])
        self.event_callback = lambda _e: None
        self.client = DummyClient()
        self.model = "m"
        self.max_tokens = 256

    def _ensure_project_memory_and_plan(self, _instruction: str) -> None:
        return None

    def _execute_tool_with_policy(self, _name: str, _inp: dict, _tool_id: str, _msg_count: int):
        return {"ok": True}


def test_weak_search_default_runtime_everywhere(tmp_path: Path):
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

    interactive = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    benchmark = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, benchmark_config=BenchmarkRuntimeConfig(enabled=True))
    assert interactive.runtime == "weak-search"
    assert benchmark.runtime == "weak-search"


def test_weak_search_uses_shared_candidate_executor(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    calls: list[dict] = []

    def fake_eval(self, **kwargs):
        calls.append(kwargs)
        return CandidateExecutionResult(changed_files=["src/app.py"], diff_stats={"changed_line_count": 1}, score=0.1, attempt_category="verification_failed")

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_eval)
    out = WeakSearchController(DummyRunner(tmp_path), "fix").run()
    assert calls
    assert out["weak_search"]["candidate_patches_generated"] >= 1


def test_weak_search_attempt_requires_real_change_or_blocked_reason(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    runner = DummyRunner(tmp_path)
    ex = CandidateExecutor(runner, "fix", 20, 1)
    result = ex.evaluate_candidate(
        repo_path=tmp_path,
        objective="fix",
        suspect_region="src/app.py",
        hypothesis_id="h1",
        hypothesis="none",
        constraints={},
        runtime_profile="benchmark",
        benchmark_config=runner.benchmark_config,
        baseline_handle="clean",
        edit_budget=ex.edit_budget,
        branch_failure_history=[],
        timeout_budget_seconds=30.0,
        attempt_id="att1",
    )
    assert result.attempt_category in {"rejected_noop", "blocked_model_failure"}


def test_weak_search_blackboard_records_real_attempts(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    def fake_eval(self, **kwargs):
        return CandidateExecutionResult(
            changed_files=["src/app.py"],
            diff_stats={"changed_line_count": 2},
            patch_artifact_path=".villani_code/patches/att-1.diff",
            verification_outputs={"commands": []},
            score=0.3,
            attempt_category="verification_failed",
            prompt_summary="prompt",
            failure_signature="sig",
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fake_eval)
    WeakSearchController(DummyRunner(tmp_path), "fix").run()
    run_dir = max((tmp_path / ".villani_code" / "runs").iterdir(), key=lambda p: p.stat().st_mtime)
    text = (run_dir / "blackboard.json").read_text(encoding="utf-8")
    assert "attempt_category" in text
    assert "patch_artifact_path" in text


def test_weak_search_benchmark_noop_regression(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    monkeypatch.setattr("villani_code.localize.ranker.rank_suspects", lambda *_a, **_k: [])
    out = WeakSearchController(DummyRunner(tmp_path), "fix").run()
    assert out["weak_search"]["stop_reason"] in {"blocked", "no_progress"}


def test_weak_search_repro_uses_repro_verifier(tmp_path: Path):
    bench = BenchmarkRuntimeConfig(enabled=True, task_id="repro_case", visible_verification=["python repro.py"], allowlist_paths=["src/"], expected_files=["src/app.py"])
    controller = WeakSearchController(DummyRunner(tmp_path, bench), "fix repro")
    evidence = controller._collect_evidence()
    assert evidence.repro_commands == ["python repro.py"]


def test_weak_search_branch_pruning_on_repeated_failure_signature(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    def fail_eval(self, **kwargs):
        return CandidateExecutionResult(hard_fail=True, blocked_reason="blocked_policy", attempt_category="blocked_policy", failure_signature="same")

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fail_eval)
    out = WeakSearchController(DummyRunner(tmp_path), "fix").run()
    assert out["weak_search"]["branches_pruned"] >= 1


def test_weak_search_candidate_isolation(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")

    def fake_model_edit(self, workspace, prompt):
        f = workspace / "src" / "app.py"
        f.write_text(f.read_text() + "x=2\n", encoding="utf-8")
        return ""

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor._run_model_edit_pass", fake_model_edit)
    ex = CandidateExecutor(DummyRunner(tmp_path), "fix", 20, 1)
    r1 = ex.evaluate_candidate(repo_path=tmp_path, objective="fix", suspect_region="src/app.py", hypothesis_id="h1", hypothesis="a", constraints={}, runtime_profile="benchmark", benchmark_config=BenchmarkRuntimeConfig(enabled=True, allowlist_paths=["src/"], expected_files=["src/app.py"], visible_verification=["python -c \"print(1)\""]), baseline_handle="clean", edit_budget=ex.edit_budget, branch_failure_history=[], timeout_budget_seconds=30.0, attempt_id="a1")
    r2 = ex.evaluate_candidate(repo_path=tmp_path, objective="fix", suspect_region="src/app.py", hypothesis_id="h2", hypothesis="b", constraints={}, runtime_profile="benchmark", benchmark_config=BenchmarkRuntimeConfig(enabled=True, allowlist_paths=["src/"], expected_files=["src/app.py"], visible_verification=["python -c \"print(1)\""]), baseline_handle="clean", edit_budget=ex.edit_budget, branch_failure_history=[], timeout_budget_seconds=30.0, attempt_id="a2")
    assert r1.changed_files == ["src/app.py"]
    assert r2.changed_files == ["src/app.py"]


def test_weak_search_model_hypothesis_generation_with_fallback(tmp_path: Path):
    suspect = SuspectRegion(file="src/app.py", score=0.8)
    model_payload = {"content": [{"type": "text", "text": '{"hypotheses":[{"class":"boundary_error","text":"Guard end index.","plausibility":0.9},{"class":"contract_mismatch","text":"Return type contract mismatch.","plausibility":0.7}]}' }]}
    runner = DummyRunner(tmp_path)
    runner.client = DummyClient(model_payload)
    kept, _rej, fallback = generate_hypotheses(suspect, "fix", runner=runner)
    assert kept and not fallback

    runner2 = DummyRunner(tmp_path)
    kept2, _rej2, fallback2 = generate_hypotheses(suspect, "fix", runner=runner2)
    assert kept2 and fallback2


def test_weak_search_patch_generation_not_stubbed():
    from villani_code.runtime.schemas import HypothesisClass, HypothesisRecord

    hyp = HypothesisRecord(id="h", suspect_ref="src/app.py", text="fix edge", hypothesis_class=HypothesisClass.BOUNDARY_ERROR, plausibility_score=0.8, diversity_bucket="b")
    cands = generate_patch_candidates(hyp, "br1")
    assert cands
    assert hasattr(cands[0], "prompt_summary")
    assert not hasattr(cands[0], "changed_lines")
