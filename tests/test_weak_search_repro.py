from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutionResult
from villani_code.runtime.controller import WeakSearchController


def test_repro_tasks_use_repro_command_as_target_verifier(tmp_path):
    class DummyRunner:
        def __init__(self):
            self.repo = tmp_path
            self.benchmark_config = BenchmarkRuntimeConfig(enabled=True, task_id="repro_case", visible_verification=["python repro.py"], allowlist_paths=["src/"], expected_files=["src/a.py"])
            self.event_callback = lambda _e: None

    controller = WeakSearchController(DummyRunner(), "fix repro")
    evidence = controller._collect_evidence()
    assert evidence.repro_commands == ["python repro.py"]


def test_repro_fingerprint_repeat_prunes_branch(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("x=1\n", encoding="utf-8")
    events = []

    class DummyRunner:
        def __init__(self):
            self.repo = tmp_path
            self.benchmark_config = BenchmarkRuntimeConfig(enabled=True, task_id="repro_case", visible_verification=["python repro.py"], allowlist_paths=["src/"], expected_files=["src/a.py"])
            self.event_callback = events.append

    def fail_eval(self, **kwargs):
        return CandidateExecutionResult(
            changed_files=["src/a.py"],
            diff_stats={"changed_line_count": 1},
            patch_artifact_path=".villani_code/patches/a.diff",
            verification_outputs={"summary": "verification_failed", "repro_fingerprint": "same"},
            score=0.2,
            attempt_category="verification_failed",
            failure_signature="same",
            success=False,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", fail_eval)
    out = WeakSearchController(DummyRunner(), "fix repro").run()
    assert out["weak_search"]["branches_pruned"] >= 1
    assert any(e.get("type") == "branch_pruned" for e in events)
