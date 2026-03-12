from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutionResult
from villani_code.runtime.controller import WeakSearchController
from villani_code.runtime.schemas import SuspectRegion


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
            task_id="task-target-selection",
            allowlist_paths=["tests/", "src/"],
            expected_files=["src/app/config.py"],
            max_files_touched=3,
            visible_verification=["pytest -q tests/test_config.py::test_precedence"],
            task_family="bugfix",
            task_type="single_file_bugfix",
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None


def test_direct_repair_picks_implementation_target_before_tests(monkeypatch, tmp_path: Path):
    (tmp_path / "src" / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app" / "config.py").write_text("VALUE=1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_config.py").write_text("def test_precedence():\n    assert True\n", encoding="utf-8")

    suspects = [
        SuspectRegion(file="tests/test_config.py", score=0.9),
        SuspectRegion(file="src/app/config.py", score=0.8),
    ]
    monkeypatch.setattr("villani_code.runtime.controller.rank_suspects", lambda *_args, **_kwargs: suspects)

    captured = {}

    def fake_eval(self, **kwargs):
        captured["suspect_region"] = kwargs["target_file"]
        return CandidateExecutionResult(
            changed_files=["src/app/config.py"],
            diff_text="diff --git a/src/app/config.py b/src/app/config.py\n",
            diff_stats={"changed_line_count": 1},
            verification_outputs={"commands": [], "target_verification_passed": True, "static_sanity_passed": True},
            attempt_category="candidate_verified",
            success=True,
            score=0.9,
        )

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_direct_patch", fake_eval)
    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.commit_candidate", lambda *_args, **_kwargs: None)

    out = WeakSearchController(DummyRunner(tmp_path), "Fix precedence bug in src/app/config.py").run()
    assert captured["suspect_region"] == "src/app/config.py"
    assert out["weak_search"]["target_file"] == "src/app/config.py"
    assert out["weak_search"]["target_selection_reason"] in {
        "objective_explicit_implementation_file",
        "expected_single_implementation_file",
    }
