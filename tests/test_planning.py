from __future__ import annotations

from villani_code.planning import PlanRiskLevel, analyze_instruction, classify_plan_risk
from villani_code.state import Runner


class DummyClient:
    def create_message(self, payload, stream=False):
        _ = (payload, stream)
        return {"content": []}


def test_high_risk_plan_still_classified_high() -> None:
    analysis = analyze_instruction(
        "delete files and rewrite history across the repo",
        repo_map={"source_roots": ["villani_code"], "repo_shape": "single_package"},
        validation_steps=["pytest"],
    )
    risk = classify_plan_risk("delete files and rewrite history across the repo", analysis)
    assert risk == PlanRiskLevel.HIGH


def test_dependency_touching_plan_detected() -> None:
    analysis = analyze_instruction(
        "update dependencies in pyproject and lockfile",
        repo_map={"manifests": ["pyproject.toml"], "lockfiles": ["poetry.lock"]},
        validation_steps=[],
    )
    assert any(a.value == "dependency_change" for a in analysis.action_classes)


def test_planning_uses_submit_plan_artifact_not_text_json(tmp_path, monkeypatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")

    def fake_run(*_a, **_k):
        runner._finalized_plan_artifact = {
            "task_summary": "Fix planning mode",
            "candidate_files": ["villani_code/state.py", "villani_code/state_runtime.py"],
            "assumptions": ["Read-only planning mode"],
            "recommended_steps": [
                "Read villani_code/state.py to confirm /plan handoff bug",
                "Update villani_code/state_runtime.py to finalize through SubmitPlan",
                "Add tests in tests/test_plan_workflow.py for artifact finalization",
            ],
            "open_questions": [],
            "risk_level": "medium",
            "confidence_score": 0.82,
        }
        return {"response": {"content": [{"type": "text", "text": "internal drafting"}]}}

    monkeypatch.setattr(runner, "run", fake_run)
    result = runner.plan("Find biggest bug and make a plan")
    assert result.task_summary == "Fix planning mode"
    assert result.ready_to_execute is True


def test_generic_plan_artifact_is_rejected(tmp_path, monkeypatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")

    def fake_run(*_a, **_k):
        runner._finalized_plan_artifact = {
            "task_summary": "Generic",
            "candidate_files": ["villani_code/state.py", "villani_code/state_runtime.py"],
            "assumptions": ["a"],
            "recommended_steps": [
                "Inspect architecture",
                "Prioritize findings",
                "Prepare execution order",
            ],
            "open_questions": [],
        }
        return {"response": {"content": [{"type": "text", "text": "generic"}]}}

    monkeypatch.setattr(runner, "run", fake_run)
    result = runner.plan("Find biggest bug and make a plan")
    assert result.confidence_score == 0.35
    assert result.task_summary
