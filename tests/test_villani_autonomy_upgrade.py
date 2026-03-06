from __future__ import annotations

import json
from pathlib import Path

from villani_code.autonomy import Opportunity, TakeoverConfig, TaskContract
from villani_code.autonomous import VillaniModeController
from villani_code.evidence import parse_command_evidence
from villani_code.opportunities import OpportunityEngine
from villani_code.repo_map import build_structured_repo_map


class SequencedRunner:
    def __init__(self, repo: Path, steps: list[dict]) -> None:
        self.repo = repo
        self.steps = steps or [{}]
        self.index = 0

    def run(self, _prompt: str, **_kwargs):
        step = self.steps[min(self.index, len(self.steps) - 1)]
        self.index += 1
        for rel, content in step.get("writes", []):
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return {
            "response": {"content": [{"type": "text", "text": step.get("text", "done")}]},
            "transcript": {"tool_results": step.get("tool_results", [])},
            "execution": {
                "turns_used": 1,
                "tool_calls_used": 0,
                "elapsed_seconds": 0.01,
                "terminated_reason": step.get("terminated_reason", "completed"),
                "intentional_changes": step.get("intentional_changes", []),
                "validation_artifacts": step.get("validation_artifacts", []),
                "runner_failures": step.get("runner_failures", []),
                "inspection_summary": step.get("inspection_summary", ""),
            },
        }


class SequencedPlanner:
    def __init__(self, waves: list[list[Opportunity]]) -> None:
        self.waves = waves
        self.calls = 0

    def build_repo_summary(self) -> str:
        return "summary"

    def discover_opportunities(self) -> list[Opportunity]:
        idx = min(self.calls, len(self.waves) - 1)
        self.calls += 1
        return self.waves[idx]


def _op(title: str, category: str = "validation", confidence: float = 0.8, contract: str = TaskContract.VALIDATION.value) -> Opportunity:
    return Opportunity(
        title=title,
        category=category,
        priority=0.85,
        confidence=confidence,
        affected_files=["villani_code/autonomous.py"],
        evidence="evidence",
        blast_radius="small",
        proposed_next_action="act",
        task_contract=contract,
        validation_strategy=["pytest -q"] if "test" in title.lower() else ["python -c 'import villani_code'"],
    )


def test_opportunity_engine_generates_multiple_categories(tmp_path: Path) -> None:
    (tmp_path / "villani_code").mkdir()
    (tmp_path / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "villani_code" / "cli.py").write_text("import os\nimport sys\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cli.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("$ pytest -q\nTODO: tighten docs\n", encoding="utf-8")

    repo_map = build_structured_repo_map(tmp_path)
    candidates = OpportunityEngine(repo_map).generate()

    categories = {c.category for c in candidates}
    assert len(candidates) >= 3
    assert "validation" in categories
    assert "investigation" in categories


def test_repo_map_detects_core_surfaces(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "__main__.py").write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pkg.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "usage.md").write_text("$ python -m pkg --help\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "workflows").mkdir()
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")

    repo_map = build_structured_repo_map(tmp_path)

    assert "pkg" in repo_map.packages
    assert repo_map.tests
    assert repo_map.docs
    assert "pyproject.toml" in repo_map.config_files
    assert repo_map.entrypoints
    assert repo_map.ci_files


def test_followup_policy_after_import_validation_prefers_tests(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    runner = SequencedRunner(tmp_path, [{"validation_artifacts": ["python -c 'import villani_code' (exit=0)"]}])
    controller = VillaniModeController(runner, tmp_path, takeover_config=TakeoverConfig(max_waves=1))
    planner = SequencedPlanner([[_op("Validate baseline importability")]])
    controller.planner = planner

    summary = controller.run()

    memory = json.loads((tmp_path / ".villani_code" / "working_memory.json").read_text(encoding="utf-8"))
    backlog_titles = [b["title"] for b in memory["backlog"]]
    assert "Run baseline tests" in backlog_titles or "Validate baseline importability" in summary["selected_tasks"]


def test_investigation_tasks_survive_lower_threshold(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner(tmp_path, [{}]), tmp_path, takeover_config=TakeoverConfig(min_confidence=0.7, max_waves=1))
    controller.planner = SequencedPlanner([[_op("Investigate docs/code mismatch", category="investigation", confidence=0.52, contract=TaskContract.INSPECTION.value)]])
    summary = controller.run()
    assert summary["tasks_attempted"]


def test_no_premature_stop_when_more_clear_work_exists(tmp_path: Path) -> None:
    runner = SequencedRunner(tmp_path, [
        {"validation_artifacts": ["python -c 'import villani_code' (exit=0)"]},
        {"validation_artifacts": ["pytest -q (exit=0)"]},
    ])
    controller = VillaniModeController(runner, tmp_path, takeover_config=TakeoverConfig(max_waves=2))
    controller.planner = SequencedPlanner([
        [_op("Validate baseline importability")],
        [_op("Run baseline tests")],
    ])
    summary = controller.run()
    assert len(summary["tasks_attempted"]) >= 2


def test_working_memory_persistence_contains_required_fields(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner(tmp_path, [{"inspection_summary": "checked"}]), tmp_path, takeover_config=TakeoverConfig(max_waves=1))
    controller.planner = SequencedPlanner([[_op("Inspect TODO hotspots", category="investigation", contract=TaskContract.INSPECTION.value)]])
    controller.run()
    memory = json.loads((tmp_path / ".villani_code" / "working_memory.json").read_text(encoding="utf-8"))
    assert "backlog" in memory
    assert "completed_tasks" in memory
    assert "validation_receipts" in memory
    assert "blockers" in memory
    assert "next_recommended_actions" in memory


def test_critic_flags_repeated_no_progress(tmp_path: Path) -> None:
    runner = SequencedRunner(tmp_path, [{"terminated_reason": "model_idle"}, {"terminated_reason": "model_idle"}])
    controller = VillaniModeController(runner, tmp_path, takeover_config=TakeoverConfig(max_waves=2))
    controller.planner = SequencedPlanner([[_op("Validate baseline importability")], [_op("Validate baseline importability")]])
    summary = controller.run()
    verdicts = [t.get("critic_verdict", "") for t in summary["tasks_attempted"]]
    assert any("no-progress" in v for v in verdicts)


def test_stop_reason_transparency_lists_exhausted_categories(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner(tmp_path, [{}]), tmp_path, takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.95))
    controller.planner = SequencedPlanner([[_op("Low confidence", confidence=0.5)]])
    summary = controller.run()
    assert "No remaining opportunities above confidence threshold" in summary["done_reason"]
    assert ";" in summary["stop_detail"]


def test_evidence_json_normalization_regression_guard() -> None:
    content = '{"command":"pytest -q","exit":0}'
    records = parse_command_evidence(content)
    assert records[0]["command"] == "pytest -q"
    assert records[0]["exit"] == 0


def test_user_facing_summary_contains_backlog_selected_and_stop(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner(tmp_path, [{"inspection_summary": "done"}]), tmp_path, takeover_config=TakeoverConfig(max_waves=1))
    controller.planner = SequencedPlanner([[_op("Inspect TODO hotspots", category="investigation", contract=TaskContract.INSPECTION.value)]])
    summary = controller.run()
    text = controller.format_summary(summary)
    assert "# Villani mode summary" in text
    assert "backlog_count" in text
    assert "selected_tasks" in text
    assert "Done reason:" in text
