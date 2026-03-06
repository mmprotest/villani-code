from __future__ import annotations

from pathlib import Path

from villani_code.autonomy import (
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

    def run(self, _prompt: str):
        return {
            "response": {"content": [{"type": "text", "text": "done"}]},
            "transcript": {"tool_results": []},
        }


def test_verification_result_status_transitions(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n", encoding="utf-8")
    engine = VerificationEngine(tmp_path)

    passed = engine.verify("goal", ["a.py"], [{"command": "python -m compileall -q .", "exit": 0}])
    assert passed.status in {VerificationStatus.PASS, VerificationStatus.UNCERTAIN}

    failed = engine.verify("goal", ["missing.py"], [{"command": "pytest -q", "exit": 1}])
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
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    planner = TakeoverPlanner(tmp_path)
    ops = planner.discover_opportunities()
    assert ops
    scores = [o.priority * 0.7 + o.confidence * 0.3 for o in ops]
    assert scores == sorted(scores, reverse=True)

    controller = VillaniModeController(StubRunner(tmp_path), tmp_path, takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.95))
    summary = controller.run()
    assert "done_reason" in summary


def test_wave_execution_limits(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("TODO\n", encoding="utf-8")
    controller = VillaniModeController(StubRunner(tmp_path), tmp_path, takeover_config=TakeoverConfig(max_waves=1, max_commands_per_wave=1, max_files_per_wave=5))
    summary = controller.run()
    waves = summary.get("completed_waves", [])
    assert len(waves) <= 1


def test_confidence_downgrade_behavior(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("# TODO\n", encoding="utf-8")
    engine = VerificationEngine(tmp_path)
    result = engine.verify("goal", ["x.py"], [{"command": "pytest -q", "exit": 0}])
    assert result.confidence_score < 0.95
