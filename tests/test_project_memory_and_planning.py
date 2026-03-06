from pathlib import Path

from villani_code.planning import PlanAnalysis, PlanRiskLevel, classify_plan_risk, compact_failure_output, generate_execution_plan
from villani_code.project_memory import ensure_project_memory, init_project_memory, load_validation_config
from villani_code.validation_loop import infer_targeted_command, select_validation_steps


def test_init_creates_villani_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    files = init_project_memory(tmp_path)
    assert (tmp_path / ".villani").exists()
    assert all(path.exists() for path in files.values())


def test_lazy_init(tmp_path: Path) -> None:
    ensure_project_memory(tmp_path)
    assert (tmp_path / ".villani" / "validation.json").exists()


def test_deterministic_risk_classification() -> None:
    analysis = PlanAnalysis(dependency_change=True)
    assert classify_plan_risk("upgrade dependencies", analysis) == PlanRiskLevel.HIGH
    analysis2 = PlanAnalysis(touches_multiple_files=True)
    assert classify_plan_risk("refactor module", analysis2) == PlanRiskLevel.MEDIUM


def test_plan_contains_contract_fields(tmp_path: Path) -> None:
    plan = generate_execution_plan("fix failing tests", tmp_path, {"manifests": ["pyproject.toml"], "config_files": [], "source_roots": ["src"]}, ["pytest"])
    payload = plan.to_dict()
    for key in ["task_goal", "assumptions", "relevant_files", "proposed_actions", "risks", "validation_steps", "done_criteria", "risk_level"]:
        assert key in payload


def test_validation_selection_and_targeting(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    init_project_memory(tmp_path)
    cfg = load_validation_config(tmp_path)
    selected = select_validation_steps(cfg, ["tests/test_x.py"])
    assert selected
    test_step = next((s for s in selected if s.kind == "test"), None)
    if test_step:
        cmd = infer_targeted_command(test_step, ["tests/test_x.py"])
        assert "tests/test_x.py" in cmd


def test_failure_compaction() -> None:
    raw = "\n".join(f"line {i}" for i in range(200))
    compact = compact_failure_output(raw)
    assert len(compact) < len(raw)
    assert "line 0" in compact
