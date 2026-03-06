from __future__ import annotations

from pathlib import Path

from villani_code.autonomy import Opportunity, TakeoverConfig, TaskContract
from villani_code.autonomous import VillaniModeController
from villani_code.shells import (
    ShellFamily,
    baseline_import_validation_command,
    classify_shell_portability_failure,
    normalize_command_for_shell,
)


class SequencedRunner:
    def __init__(self, steps: list[dict]) -> None:
        self.steps = steps
        self.index = 0

    def run(self, _prompt: str, **_kwargs):
        step = self.steps[min(self.index, len(self.steps) - 1)]
        self.index += 1
        return {
            "response": {"content": [{"type": "text", "text": step.get("text", "done")}]},
            "transcript": {"tool_results": step.get("tool_results", [])},
            "execution": {
                "terminated_reason": "completed",
                "turns_used": 1,
                "tool_calls_used": 0,
                "elapsed_seconds": 0.1,
                "intentional_changes": step.get("intentional_changes", []),
                "incidental_changes": [],
                "all_changes": step.get("intentional_changes", []),
                "validation_artifacts": step.get("validation_artifacts", []),
                "inspection_summary": step.get("inspection_summary", ""),
                "runner_failures": step.get("runner_failures", []),
                "intended_targets": [],
                "before_contents": {},
            },
        }


class Planner:
    def __init__(self, waves: list[list[Opportunity]]) -> None:
        self.waves = waves
        self.calls = 0

    def build_repo_summary(self) -> str:
        return "files=10 py=4 tests=1 docs=2 has_tests=1"

    def discover_opportunities(self) -> list[Opportunity]:
        idx = min(self.calls, len(self.waves) - 1)
        self.calls += 1
        return self.waves[idx]


def _op(title: str, category: str = "validation", contract: str = TaskContract.VALIDATION.value) -> Opportunity:
    return Opportunity(
        title=title,
        category=category,
        priority=0.9,
        confidence=0.9,
        affected_files=["villani_code/__init__.py"],
        evidence="evidence",
        blast_radius="small",
        proposed_next_action="act",
        task_contract=contract,
    )


def test_windows_shell_portability_patterns_are_not_emitted() -> None:
    cmd = normalize_command_for_shell("python -c \"import x\" && echo EXIT_CODE: $? | tail -20", ShellFamily.WINDOWS)
    assert "$?" not in cmd
    assert "tail -20" not in cmd
    assert "&& echo" not in cmd


def test_posix_shell_command_is_kept_valid() -> None:
    cmd = baseline_import_validation_command(ShellFamily.POSIX)
    assert cmd.startswith("python -c")
    assert "import villani_code" in cmd


def test_session_satisfied_dedup_skips_reselecting_import_validation(tmp_path: Path) -> None:
    (tmp_path / "villani_code").mkdir()
    (tmp_path / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    controller._satisfied_task_keys["validate-baseline-importability"] = controller._repo_fingerprint_for_task("validate-baseline-importability")

    candidates = controller._build_wave_candidates([_op("Validate baseline importability")])
    assert candidates == []


def test_invalidation_reenables_satisfied_task(tmp_path: Path) -> None:
    pkg = tmp_path / "villani_code"
    pkg.mkdir()
    init = pkg / "__init__.py"
    init.write_text("A=1\n", encoding="utf-8")
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    key = "validate-baseline-importability"
    controller._satisfied_task_keys[key] = controller._repo_fingerprint_for_task(key)
    init.write_text("A=2\n", encoding="utf-8")

    assert controller._is_task_satisfied(key) is False


def test_followups_are_inserted_into_real_backlog(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "villani_code").mkdir()
    (tmp_path / "villani_code" / "__init__.py").write_text("", encoding="utf-8")

    runner = SequencedRunner([
        {"validation_artifacts": ["python -c 'import villani_code' (exit=0)"]},
        {"validation_artifacts": ["pytest -q (exit=0)"]},
    ])
    controller = VillaniModeController(runner, tmp_path, takeover_config=TakeoverConfig(max_waves=2))
    controller.planner = Planner([[ _op("Validate baseline importability") ], []])
    controller.run()

    titles = [item["title"] for item in controller._backlog_insertions]
    assert "Run baseline tests" in titles
    assert any(t in titles for t in {"Validate CLI entrypoint", "Validate documented commands/examples"})


def test_followup_priority_beats_repeat_import_validation(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    ranked = controller._build_wave_candidates([
        _op("Validate baseline importability", category="validation"),
        _op("Run baseline tests", category="followup_tests"),
    ])
    assert ranked[0].title == "Run baseline tests"


def test_stop_blocked_while_tests_unexamined(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "villani_code").mkdir()
    (tmp_path / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    runner = SequencedRunner([{ "validation_artifacts": ["python -c 'import villani_code' (exit=0)"] }])
    controller = VillaniModeController(runner, tmp_path, takeover_config=TakeoverConfig(max_waves=2))
    controller.planner = Planner([[_op("Validate baseline importability")], []])
    summary = controller.run()

    assert "tests examined:" in summary["done_reason"]


def test_stop_reason_reports_category_exhaustion(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner([]), tmp_path, takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.99))
    controller.planner = Planner([[_op("Validate baseline importability", category="validation")]])
    summary = controller.run()
    assert "tests examined:" in summary["done_reason"]
    assert "docs examined:" in summary["done_reason"]
    assert "entrypoints examined:" in summary["done_reason"]


def test_critic_marks_stale_repetition() -> None:
    controller = VillaniModeController(SequencedRunner([]), Path("."))
    task = type("T", (), {"attempts": 2, "intentional_changes": [], "validation_artifacts": []})()
    assert controller._is_stale_repeat(task) is True


def test_shell_portability_failure_classification() -> None:
    assert classify_shell_portability_failure(["python -c x && echo EXIT_CODE: $? | tail -20"])


def test_working_memory_contains_required_fields(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner([]), tmp_path, takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.99))
    controller.planner = Planner([[]])
    summary = controller.run()
    memory = summary["working_memory"]
    assert "satisfied_task_keys" in memory
    assert "backlog_insertions" in memory
    assert "category_examination_state" in memory
    assert "stop_decision_rationale" in memory


def test_evidence_json_parsing_regression_still_works(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    commands = controller._extract_commands(
        {
            "transcript": {
                "tool_results": [
                    {
                        "content": '{"command":"python -c \\\"import villani_code\\\"","exit_code":0}'
                    }
                ]
            }
        }
    )
    assert commands == [{"command": 'python -c "import villani_code"', "exit": 0}]
