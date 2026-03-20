from __future__ import annotations

import json
from pathlib import Path


from villani_code.planning import PlanRiskLevel, generate_execution_plan
from villani_code.project_memory import SessionState, init_project_memory, load_repo_map, load_validation_config
from villani_code.session_state import SessionMemory
from villani_code.state import Runner
from villani_code import state_runtime
from villani_code.validation_loop import (
    infer_targeted_command,
    plan_validation,
    summarize_validation_failure,
)


class DummyClient:
    def __init__(self, content: list[dict] | None = None) -> None:
        self.content = content or [{"type": "text", "text": "ok"}]

    def create_message(self, _payload, stream=False):
        return {"id": "x", "role": "assistant", "content": self.content}


def _seed_repo(tmp_path: Path) -> None:
    (tmp_path / "villani_code").mkdir()
    (tmp_path / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "villani_code" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text("from villani_code.mod import f\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("docs", encoding="utf-8")


def test_grounded_risk_classification_with_repo_evidence(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    loaded = load_repo_map(tmp_path)
    plan = generate_execution_plan("upgrade dependency in pyproject.toml", tmp_path, loaded, ["pytest"])
    assert plan.risk_level == PlanRiskLevel.HIGH
    assert "dependency_change" in plan.action_classes
    assert "pyproject.toml" in plan.relevant_files


def test_candidate_target_and_scope_inference(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    loaded = load_repo_map(tmp_path)
    plan = generate_execution_plan("refactor villani_code and tests", tmp_path, loaded, ["pytest"])
    assert plan.estimated_scope in {"narrow_multi_file", "broad_multi_file", "package_wide", "repo_wide"}
    assert any(p.startswith("villani_code") or p.startswith("tests") for p in plan.relevant_files)


def test_init_outputs_deterministic(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    first = (tmp_path / ".villani" / "repo_map.json").read_text(encoding="utf-8")
    init_project_memory(tmp_path)
    second = (tmp_path / ".villani" / "repo_map.json").read_text(encoding="utf-8")
    assert first == second


def test_richer_repo_discovery(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    repo_map = load_repo_map(tmp_path)
    assert "languages" in repo_map and "python" in repo_map["languages"]
    assert repo_map["source_roots"]
    assert repo_map["test_roots"]
    assert repo_map["repo_shape"] in {"single_package", "multi_root"}
    assert "package_test_relationships" in repo_map


def test_validation_targeting_changed_test_file(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    cfg = load_validation_config(tmp_path)
    step = next(s for s in cfg.steps if s.name == "pytest-targeted")
    cmd = infer_targeted_command(step, ["tests/test_mod.py"], {"test_roots": ["tests"]})
    assert "tests/test_mod.py" in cmd


def test_validation_targeting_changed_source_file(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    cfg = load_validation_config(tmp_path)
    step = next(s for s in cfg.steps if s.name == "pytest-targeted")
    cmd = infer_targeted_command(step, ["villani_code/mod.py"], {"test_roots": ["tests"]})
    assert "test_mod.py" in cmd


def test_validation_docs_only_and_manifest_escalation(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    cfg = load_validation_config(tmp_path)

    docs_plan = plan_validation(cfg, ["README.md", "docs/guide.md"])
    assert all(s.step.kind in {"format", "lint", "inspection"} for s in docs_plan.selected_steps)

    manifest_plan = plan_validation(cfg, ["pyproject.toml"], change_impact="config_or_manifest")
    manifest_kinds = {s.step.kind for s in manifest_plan.selected_steps}
    assert "test" in manifest_kinds


def test_cost_aware_ordering_and_failure_summary(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    cfg = load_validation_config(tmp_path)
    plan = plan_validation(cfg, ["villani_code/mod.py"])
    costs = [s.step.cost_level for s in plan.selected_steps]
    assert costs == sorted(costs)

    failure = summarize_validation_failure("pytest", "\n".join([f"line {i}" for i in range(120)]), "")
    assert "pytest failed" == failure.headline
    assert len(failure.compact_output) < 1500


def test_dedicated_repair_executor_is_bounded(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner.max_repair_attempts = 2

    validation_path = tmp_path / ".villani" / "validation.json"
    data = json.loads(validation_path.read_text(encoding="utf-8"))
    data["steps"] = [{"name": "always-fail", "command": "python -c 'import sys;sys.exit(1)'", "kind": "test", "cost_level": 1, "is_mutating": False, "enabled": True, "scope_hint": "repo", "language_family": "python", "target_strategy": "full", "escalation_role": "broad_safety_net", "typical_trigger_conditions": ["broad_change"]}]
    validation_path.write_text(json.dumps(data), encoding="utf-8")

    result = state_runtime.run_post_execution_validation(runner, ["villani_code/mod.py"])
    assert "bounded repair attempts" in result
    state_payload = json.loads((tmp_path / ".villani" / "session_state.json").read_text(encoding="utf-8"))
    assert "attempted_fixes" in state_payload
    assert "repair loop exhausted" in state_payload["attempted_fixes"][0]


def test_project_memory_update_persists_minimal_session_subset(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    init_project_memory(tmp_path)
    state = SessionState(
        task_summary="task",
        plan_summary="plan",
        plan_risk="medium",
        grounding_evidence_summary=["evidence"],
        action_classes=["code_edit"],
        estimated_scope="narrow_multi_file",
        change_impact="source_only",
        candidate_targets_summary=["villani_code/mod.py"],
        affected_files=["villani_code/mod.py"],
        validation_plan_summary=["pytest-targeted"],
        validation_summary="passed",
        outcome_status="success",
    )
    from villani_code.project_memory import update_session_state

    update_session_state(tmp_path, state)
    payload = json.loads((tmp_path / ".villani" / "session_state.json").read_text(encoding="utf-8"))
    memory = SessionMemory.from_dict(payload)
    assert memory.current_goal == "task"
    assert memory.current_plan == ["plan"]
    assert memory.changed_files == ["villani_code/mod.py"]
    assert memory.latest_error == "passed"
    assert memory.updated_at


def test_autonomous_high_risk_auto_approved(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=True)
    events: list[dict] = []
    runner.event_callback = events.append
    state_runtime.ensure_project_memory_and_plan(runner, "delete files and rewrite history")
    assert any(e.get("type") == "plan_auto_approved" for e in events)
    assert not any(e.get("type") == "plan_aborted" for e in events)


def test_interactive_plan_approval_still_works(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    asked = {"count": 0}

    def approve(_name: str, _payload: dict) -> bool:
        asked["count"] += 1
        return True

    runner.approval_callback = approve
    state_runtime.ensure_project_memory_and_plan(runner, "refactor villani_code and tests")
    assert asked["count"] == 1
