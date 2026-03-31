from pathlib import Path

from villani_code.planning import PlanRiskLevel, classify_plan_risk, compact_failure_output, generate_execution_plan, analyze_instruction
from villani_code.project_memory import ensure_project_memory, init_project_memory, load_validation_config
from villani_code.runtime_paths import get_memory_dir
from villani_code.validation_loop import infer_targeted_command, select_validation_steps


def test_init_creates_villani_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    files = init_project_memory(tmp_path)
    assert get_memory_dir(tmp_path).exists()
    assert all(path.exists() for path in files.values())


def test_lazy_init(tmp_path: Path) -> None:
    ensure_project_memory(tmp_path)
    assert (get_memory_dir(tmp_path) / "validation.json").exists()


def test_deterministic_risk_classification() -> None:
    analysis = analyze_instruction("upgrade dependencies in pyproject.toml", {"manifests": ["pyproject.toml"]}, ["pytest"])
    assert classify_plan_risk("upgrade dependencies", analysis) == PlanRiskLevel.HIGH
    analysis2 = analyze_instruction("fix test failure", {"source_roots": ["src"], "test_roots": ["tests"]}, ["pytest"])
    assert classify_plan_risk("fix test failure", analysis2) in {PlanRiskLevel.MEDIUM, PlanRiskLevel.HIGH}


def test_plan_contains_contract_fields(tmp_path: Path) -> None:
    plan = generate_execution_plan("fix failing tests", tmp_path, {"manifests": ["pyproject.toml"], "config_files": [], "source_roots": ["src"], "test_roots": ["tests"]}, ["pytest"])
    payload = plan.to_dict()
    for key in ["task_goal", "assumptions", "relevant_files", "proposed_actions", "risks", "validation_steps", "done_criteria", "risk_level", "grounding_evidence", "candidate_targets", "change_impact", "risk_assessment"]:
        assert key in payload


def test_validation_selection_and_targeting(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    init_project_memory(tmp_path)
    cfg = load_validation_config(tmp_path)
    selected = select_validation_steps(cfg, ["tests/test_x.py"])
    assert selected
    test_step = next((s for s in selected if s.kind == "test"), None)
    if test_step:
        cmd = infer_targeted_command(test_step, ["tests/test_x.py"], {"test_roots": ["tests"]})
        assert "tests/test_x.py" in cmd


def test_failure_compaction() -> None:
    raw = "\n".join(f"line {i}" for i in range(200))
    compact = compact_failure_output(raw)
    assert len(compact) < len(raw)
    assert "line 0" in compact
