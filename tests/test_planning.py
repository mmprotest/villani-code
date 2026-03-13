from __future__ import annotations

import json

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


def test_repo_review_plan_avoids_generic_scaffold(tmp_path) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    result = runner.plan("Find ways to improve this repo")
    forbidden = {
        "Survey high-signal areas first: high-signal files from the repo map.",
        "Audit command routing, runner orchestration, and state transitions for correctness and UX consistency.",
        "Identify concrete improvement candidates, ranked by user impact and implementation risk.",
    }
    assert not forbidden.issubset(set(result.recommended_steps))


def test_repo_review_plan_ready_without_unnecessary_clarification(tmp_path) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    result = runner.plan("Find ways to improve this repo")
    assert result.ready_to_execute is True
    assert result.open_questions == []


def test_plan_payload_dict_is_rendered_cleanly(tmp_path, monkeypatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")

    payload = {
        "task_summary": "Improve /plan behavior",
        "candidate_files": [{"path": "villani_code/state.py", "improvement_focus": "planning flow"}],
        "assumptions": [{"risk": "regression", "mitigation": "tests"}],
        "recommended_steps": [{"priority": "P1", "action": "Fix one-shot /plan"}],
        "risks": [{"risk": "UX regression", "mitigation": "status checks"}],
        "validation_approach": [{"check": "pytest tests/test_plan_workflow.py"}],
        "open_questions": [],
    }

    monkeypatch.setattr("villani_code.state._collect_planning_evidence", lambda *_a, **_k: [{"path": "villani_code/state.py", "excerpt": "def plan"}])
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": json.dumps(payload)}]}},
    )

    result = runner.plan("Find ways to improve this repo")
    assert all("{" not in line and "}" not in line for line in result.candidate_files)
    assert all("{" not in line and "}" not in line for line in result.recommended_steps)


def test_planning_collects_file_evidence(tmp_path, monkeypatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    observed = []

    def fake_collect(repo, instruction, repo_map):
        _ = (instruction, repo_map)
        observed.append(str(repo / "villani_code/state.py"))
        return [{"path": "villani_code/state.py", "excerpt": "def plan"}]

    monkeypatch.setattr("villani_code.state._collect_planning_evidence", fake_collect)
    runner.plan("Find ways to improve this repo")
    assert observed
