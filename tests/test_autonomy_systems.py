from __future__ import annotations

import subprocess
from pathlib import Path

from villani_code.autonomy import (
    Opportunity,
    FailureCategory,
    FailureClassifier,
    TakeoverConfig,
    TakeoverPlanner,
    VerificationEngine,
    VerificationStatus,
)
from villani_code.autonomous import VillaniModeController


class StubRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def run(self, _prompt: str, **_kwargs):
        return {
            "response": {"content": [{"type": "text", "text": "done"}]},
            "transcript": {"tool_results": []},
        }


class PromptCapturingRunner(StubRunner):
    def __init__(self, repo: Path) -> None:
        super().__init__(repo)
        self.prompts: list[str] = []

    def run(self, prompt: str, **_kwargs):
        self.prompts.append(prompt)
        return super().run(prompt, **_kwargs)


def test_verification_result_status_transitions(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n", encoding="utf-8")
    engine = VerificationEngine(tmp_path)

    passed = engine.verify(
        "goal", ["a.py"], [{"command": "python -m compileall -q .", "exit": 0}]
    )
    assert passed.status in {VerificationStatus.PASS, VerificationStatus.UNCERTAIN}

    failed = engine.verify(
        "goal", ["missing.py"], [{"command": "pytest -q", "exit": 1}]
    )
    assert failed.status == VerificationStatus.FAIL
    assert failed.findings


def test_failure_classifier_logic_and_repeated_shift() -> None:
    classifier = FailureClassifier()
    first = classifier.classify("tool failed", "permission denied")
    assert first.category == FailureCategory.PERMISSION_OR_SANDBOX_ISSUE

    a = classifier.classify("tool failed", "unknown symbol")
    b = classifier.classify("tool failed", "unknown symbol")
    c = classifier.classify("tool failed", "unknown symbol")
    assert a.category == FailureCategory.MISSING_CONTEXT
    assert b.occurrence_count == 2
    assert c.category == FailureCategory.REPEATED_NO_PROGRESS


def test_takeover_opportunity_ranking_and_stop_conditions(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("TODO sync docs\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()
    assert ops
    scores = [o.priority * 0.7 + o.confidence * 0.3 for o in ops]
    assert scores == sorted(scores, reverse=True)

    controller = VillaniModeController(
        StubRunner(tmp_path),
        tmp_path,
        takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.95),
    )
    summary = controller.run()
    assert "done_reason" in summary


def test_python_repo_without_tests_creates_bootstrap_tests_opportunity(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8"
    )

    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()

    assert any(op.title == "Bootstrap minimal tests" for op in ops)


def test_python_repo_also_gets_baseline_validation_opportunity(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")

    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()

    assert any(op.title == "Validate baseline importability" for op in ops)


def test_sparse_docs_repo_gets_docs_coverage_gap_opportunity(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")

    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()

    assert any(op.title == "Audit missing usage docs" for op in ops)


def test_fallback_opportunity_created_when_no_other_heuristics_fire(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")

    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()

    assert [op.title for op in ops] == [
        "Inspect repo for highest-leverage small improvement"
    ]


def test_done_reason_no_opportunities_discovered(tmp_path: Path) -> None:
    controller = VillaniModeController(StubRunner(tmp_path), tmp_path)
    controller.planner = TakeoverPlanner(tmp_path, enable_fallback=False)

    summary = controller.run()

    assert summary["done_reason"] == "No opportunities discovered."


def test_done_reason_below_threshold_is_distinct(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8"
    )
    controller = VillaniModeController(
        StubRunner(tmp_path),
        tmp_path,
        takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.99),
    )

    summary = controller.run()

    assert (
        summary["done_reason"]
        == "No remaining opportunities above confidence threshold."
    )


def test_takeover_summary_separates_preexisting_changes_from_new_changes(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    (tmp_path / "README.md").write_text("dirty\n", encoding="utf-8")

    controller = VillaniModeController(
        StubRunner(tmp_path),
        tmp_path,
        takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.99),
    )
    summary = controller.run()

    assert summary["preexisting_changes"] == ["README.md"]
    assert summary["files_changed"] == []


def test_no_tasks_run_means_no_intentional_or_incidental_changes(
    tmp_path: Path,
) -> None:
    controller = VillaniModeController(
        StubRunner(tmp_path),
        tmp_path,
        takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.99),
    )
    summary = controller.run()

    assert summary["intentional_changes"] == []
    assert summary["incidental_changes"] == []


def test_rank_order_prefers_real_python_repo_work_over_docs_drift(
    tmp_path: Path,
) -> None:
    planner = TakeoverPlanner(tmp_path)
    ops = [
        Opportunity(
            title="Audit docs drift",
            category="stale_docs",
            priority=0.95,
            confidence=0.95,
            affected_files=["README.md"],
            evidence="x",
            blast_radius="small",
            proposed_next_action="x",
            task_contract="effectful",
        ),
        Opportunity(
            title="Bootstrap minimal tests",
            category="testing",
            priority=0.78,
            confidence=0.78,
            affected_files=["pkg/a.py"],
            evidence="x",
            blast_radius="small",
            proposed_next_action="x",
            task_contract="effectful",
        ),
    ]

    ranked = sorted(ops, key=planner._rank_key)
    assert ranked[0].title == "Bootstrap minimal tests"


def test_fallback_inspection_is_bounded(tmp_path: Path) -> None:
    runner = PromptCapturingRunner(tmp_path)
    controller = VillaniModeController(runner, tmp_path)
    task = next(
        t
        for t in controller.generate_candidates(controller.inspect_repo())
        if t.title == "Inspect repo for highest-leverage small improvement"
    )

    controller._execute_task(task)

    prompt = runner.prompts[-1]
    assert "1) top-level README.md or README.rst" in prompt
    assert "4) up to 3 representative Python source files" in prompt


def test_wave_execution_limits(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("TODO\n", encoding="utf-8")
    controller = VillaniModeController(
        StubRunner(tmp_path),
        tmp_path,
        takeover_config=TakeoverConfig(
            max_waves=1, max_commands_per_wave=1, max_files_per_wave=5
        ),
    )
    summary = controller.run()
    waves = summary.get("completed_waves", [])
    assert len(waves) <= 1


def test_confidence_downgrade_behavior(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("# TODO\n", encoding="utf-8")
    engine = VerificationEngine(tmp_path)
    result = engine.verify("goal", ["x.py"], [{"command": "pytest -q", "exit": 0}])
    assert result.confidence_score < 0.95


def test_takeover_planner_falls_back_when_rg_missing(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "README.md").write_text("TODO sync docs\n", encoding="utf-8")
    monkeypatch.setattr("villani_code.autonomy.shutil.which", lambda _name: None)

    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()

    assert any(op.category == "todo_fixme_cluster" for op in ops)


def test_takeover_planner_uses_rg_when_available(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("villani_code.autonomy.shutil.which", lambda _name: "/fake/rg")

    class StubResult:
        returncode = 0
        stdout = "README.md:12: TODO sync docs\n"

    monkeypatch.setattr(
        "villani_code.autonomy.subprocess.run", lambda *args, **kwargs: StubResult()
    )

    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()

    todo_op = next(op for op in ops if op.category == "todo_fixme_cluster")
    assert todo_op.evidence == "README.md:12: TODO sync docs"


def test_takeover_planner_handles_rg_launch_failure(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "note.txt").write_text("FIXME stabilize startup\n", encoding="utf-8")
    monkeypatch.setattr("villani_code.autonomy.shutil.which", lambda _name: "/fake/rg")

    def _raise(*_args, **_kwargs):
        raise FileNotFoundError("rg not found")

    monkeypatch.setattr("villani_code.autonomy.subprocess.run", _raise)

    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()

    todo_op = next(op for op in ops if op.category == "todo_fixme_cluster")
    assert "FIXME stabilize startup" in todo_op.evidence
