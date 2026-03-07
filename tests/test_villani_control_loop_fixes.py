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
    task = type("T", (), {"attempts": 2, "intentional_changes": [], "validation_artifacts": [], "files_changed": [], "task_key": "k"})()
    controller._lineage_last_fingerprint["k"] = controller._repo_fingerprint_for_task("k")
    controller._lineage_last_intentional_changes["k"] = tuple()
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


def test_takeover_config_defaults_are_bounded() -> None:
    cfg = TakeoverConfig()
    assert cfg.max_commands_per_wave == 2
    assert cfg.max_waves == 3
    assert cfg.max_total_task_attempts == 6
    assert cfg.min_confidence == 0.60
    assert cfg.stagnation_cycle_limit == 2


def test_planner_churn_stops_fast_with_explicit_reason(tmp_path: Path) -> None:
    events: list[dict] = []
    controller = VillaniModeController(SequencedRunner([{}]), tmp_path, event_callback=events.append, takeover_config=TakeoverConfig(max_waves=5))
    controller.planner = Planner([[], [], []])
    summary = controller.run()
    assert summary["done_reason"] == "Stopped: planner loop with no model activity."
    assert any(e.get("type") == "villani_planner_churn" for e in events)


def test_insert_followup_deduplicates_cli_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "villani_code").mkdir()
    (tmp_path / "villani_code" / "cli.py").write_text("", encoding="utf-8")
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    followup = _op("Validate CLI entrypoint", category="followup_entrypoint")
    controller._insert_followup(followup, "s1")
    controller._insert_followup(followup, "s1")
    assert [o.title for o in controller._followup_queue].count("Validate CLI entrypoint") == 1


def test_cli_followup_not_inserted_without_cli_signal(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    controller._category_state["entrypoints"] = "discovered"
    task = type("T", (), {"title": "Validate baseline importability", "status": "passed"})()
    op = _op("Validate baseline importability")
    titles = [f.title for f in controller._deterministic_followups(task, op)]
    assert "Validate CLI entrypoint" not in titles


def test_docs_followup_not_inserted_without_docs(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    controller._category_state["docs"] = "discovered"
    task = type("T", (), {"title": "Validate baseline importability", "status": "passed"})()
    op = _op("Validate baseline importability")
    titles = [f.title for f in controller._deterministic_followups(task, op)]
    assert "Validate documented commands/examples" not in titles


def test_tests_followup_not_inserted_without_real_test_files(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "notes.txt").write_text("x", encoding="utf-8")
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    controller._category_state["tests"] = "discovered"
    task = type("T", (), {"title": "Validate baseline importability", "status": "passed"})()
    op = _op("Validate baseline importability")
    titles = [f.title for f in controller._deterministic_followups(task, op)]
    assert "Run baseline tests" not in titles


def test_model_request_lifecycle_events_emitted(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = SequencedRunner([{"validation_artifacts": ["python -c 'import villani_code' (exit=0)"]}])
    controller = VillaniModeController(runner, tmp_path, event_callback=events.append)
    # reuse real task type
    from villani_code.autonomous import AutonomousTask
    real_task = AutonomousTask("1", "Validate baseline importability", "r", 1.0, 1.0, [], task_contract=TaskContract.VALIDATION.value, attempts=1)
    controller._execute_task(real_task)
    types = [e.get("type") for e in events]
    assert "villani_model_request_started" in types
    assert "villani_model_request_finished" in types


def test_stale_repeat_validation_does_not_generate_deterministic_followups(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner([]), tmp_path)
    op = _op("Validate baseline importability", contract=TaskContract.VALIDATION.value)
    from villani_code.autonomous import AutonomousTask
    task = AutonomousTask("1", "Validate baseline importability", "r", 1.0, 1.0, [], task_contract=TaskContract.VALIDATION.value, task_key=controller._task_key_for_opportunity(op), attempts=2)
    controller._lineage_last_fingerprint[task.task_key] = controller._repo_fingerprint_for_task(task.task_key)
    controller._lineage_last_intentional_changes[task.task_key] = tuple()
    task.status = "failed"
    status = controller._update_lifecycle_after_attempt(task, op)
    assert status == "exhausted"
    assert controller._followup_queue == []


def test_working_memory_contains_new_control_fields(tmp_path: Path) -> None:
    controller = VillaniModeController(SequencedRunner([]), tmp_path, takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.99))
    controller.planner = Planner([[]])
    summary = controller.run()
    memory = summary["working_memory"]
    assert "model_request_count" in memory
    assert "planner_only_cycles" in memory
    assert "followup_skip_reasons" in memory
    assert "stop_decision_kind" in memory


def test_summary_includes_control_loop_metrics() -> None:
    text = VillaniModeController.format_summary({
        "repo_summary": "x",
        "tasks_attempted": [],
        "done_reason": "Stopped: planner loop with no model activity.",
        "blockers": [],
        "preexisting_changes": [],
        "files_changed": [],
        "intentional_changes": [],
        "recommended_next_steps": [],
        "working_memory": {"model_request_count": 2, "backlog_insertions": [{"title": "x"}], "critic_outcomes": ["ok"], "stop_decision_kind": "planner_churn"},
    })
    assert "## Villani control loop" in text
    assert "model_requests: 2" in text
    assert "stop_reason:" in text
