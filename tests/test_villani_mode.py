from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from villani_code.tui.components.settings import SettingsManager
from villani_code import cli
from villani_code.autonomous import AutonomousTask, RepoSnapshot, VillaniModeController
from villani_code.permissions import Decision
from villani_code.state import Runner
from villani_code.execution import ExecutionBudget


class DummyClient:
    def create_message(self, _payload, stream):
        return {"content": [{"type": "text", "text": "ok"}]}


class StubRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls: list[str] = []

    def run(self, prompt: str, execution_budget: ExecutionBudget | None = None):
        self.calls.append(prompt)
        self.last_budget = execution_budget
        return {
            "response": {"content": [{"type": "text", "text": "done"}]},
            "transcript": {"tool_results": []},
            "execution": {
                "final_text": "done",
                "turns_used": 1,
                "tool_calls_used": 0,
                "elapsed_seconds": 0.01,
                "files_changed": [],
                "terminated_reason": "completed",
                "completed": True,
            },
        }


def test_repo_inspection_generates_candidates(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "def test_ok():\n assert True\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("TODO: improve docs\n", encoding="utf-8")
    controller = VillaniModeController(StubRunner(tmp_path), tmp_path)

    snapshot = controller.inspect_repo()
    candidates = controller.generate_candidates(snapshot)

    assert snapshot.tooling_commands
    assert candidates


def test_task_ranking_prefers_high_score(tmp_path: Path) -> None:
    controller = VillaniModeController(StubRunner(tmp_path), tmp_path)
    ranked = controller.rank_tasks(
        [
            AutonomousTask(
                "a",
                "low",
                "",
                priority=0.1,
                confidence=0.1,
                verification_plan=["echo ok"],
            ),
            AutonomousTask(
                "b",
                "high",
                "",
                priority=0.9,
                confidence=0.9,
                verification_plan=["echo ok"],
            ),
        ]
    )
    assert ranked[0].title == "high"


def test_done_state_when_no_worthwhile_candidates(tmp_path: Path) -> None:
    class NoWorkController(VillaniModeController):
        def generate_candidates(self, snapshot: RepoSnapshot):
            return [
                AutonomousTask(
                    "x",
                    "speculative",
                    "",
                    priority=0.2,
                    confidence=0.2,
                    verification_plan=[],
                )
            ]

    controller = NoWorkController(StubRunner(tmp_path), tmp_path)
    summary = controller.run()
    assert "done_reason" in summary
    assert "confidence threshold" in summary["done_reason"]


def test_runner_villani_mode_auto_approves_edits(tmp_path: Path) -> None:
    runner = Runner(
        client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=True
    )
    events: list[dict] = []
    runner.event_callback = events.append
    result = runner._execute_tool_with_policy(
        "Write", {"file_path": "a.txt", "content": "x", "mkdirs": True}, "1", 0
    )
    assert result["is_error"] is False
    assert any(e.get("type") == "approval_auto_resolved" for e in events)


def test_hard_shell_denylist_still_active_in_villani_mode(tmp_path: Path) -> None:
    runner = Runner(
        client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=True
    )
    result = runner._execute_tool_with_policy(
        "Bash",
        {"command": "curl https://example.com", "cwd": ".", "timeout_sec": 5},
        "1",
        0,
    )
    assert result["is_error"] is True


def test_cli_has_villani_mode_subcommand() -> None:
    cli_runner = CliRunner()
    result = cli_runner.invoke(cli.app, ["villani-mode", "--help"])
    assert result.exit_code == 0

def test_cli_primary_help_mentions_villani_mode() -> None:
    cli_runner = CliRunner()
    result = cli_runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "villani-mode" in result.stdout


def test_cli_takeover_alias_is_hidden_from_primary_help() -> None:
    cli_runner = CliRunner()
    top_help = cli_runner.invoke(cli.app, ["--help"])
    assert top_help.exit_code == 0
    assert "takeover" not in top_help.stdout

    alias_help = cli_runner.invoke(cli.app, ["takeover", "--help"])
    assert alias_help.exit_code == 0


def test_cli_flag_overrides_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (home / ".villani").mkdir(parents=True)
    (home / ".villani" / "settings.json").write_text(
        '{"villani_mode": true}', encoding="utf-8"
    )

    settings = SettingsManager(repo, home=home).load()
    assert settings.villani_mode is True
    assert cli._resolve_villani_flag(repo, False) is False


def test_summary_generation_includes_verification(tmp_path: Path) -> None:
    task = AutonomousTask(
        "1", "t", "r", priority=1.0, confidence=1.0, verification_plan=["echo ok"]
    )
    task.status = "passed"
    task.verification_results = [{"command": "echo ok", "exit": 0}]
    summary_text = VillaniModeController.format_summary(
        {
            "tasks_attempted": [
                {
                    "title": task.title,
                    "status": task.status,
                    "verification": task.verification_results,
                }
            ],
            "done_reason": "done",
            "blockers": [],
            "files_changed": [],
            "recommended_next_steps": [],
        }
    )
    assert "verification" in summary_text


def test_villani_mode_uses_unbounded_runner_budget(tmp_path: Path) -> None:
    runner = StubRunner(tmp_path)
    controller = VillaniModeController(runner, tmp_path)

    task = AutonomousTask(
        "1", "task", "because", priority=1.0, confidence=1.0, verification_plan=[]
    )
    controller._execute_task(task)

    assert runner.last_budget is None


def test_villani_mode_startup_without_prompt(tmp_path: Path) -> None:
    pytest.importorskip("textual")
    from villani_code.tui.app import VillaniTUI

    class MinimalRunner:
        model = "m"

        def run_villani_mode(self):
            return {"response": {"content": [{"type": "text", "text": "done"}]}}

    app = VillaniTUI(MinimalRunner(), tmp_path, villani_mode=True)
    called = {"v": False}
    app.controller.run_villani_mode = lambda: called.__setitem__("v", True)  # type: ignore[method-assign]

    import asyncio

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert called["v"] is True

    asyncio.run(run())


from villani_code.autonomy import Opportunity, TakeoverConfig, TaskContract


class SequencedRunner:
    def __init__(self, repo: Path, steps: list[dict]) -> None:
        self.repo = repo
        self.steps = steps
        self.index = 0

    def run(self, _prompt: str, execution_budget: ExecutionBudget | None = None):
        step = self.steps[min(self.index, len(self.steps) - 1)]
        self.index += 1
        for rel, content in step.get("writes", []):
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return {
            "response": {
                "content": [{"type": "text", "text": step.get("text", "done")}]
            },
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


def _op(
    title: str, contract: str = TaskContract.EFFECTFUL.value, confidence: float = 0.8
) -> Opportunity:
    return Opportunity(
        title=title,
        category="test",
        priority=0.9,
        confidence=confidence,
        affected_files=["src/app.py"],
        evidence="e",
        blast_radius="small",
        proposed_next_action="act",
        task_contract=contract,
    )


def test_failed_task_is_not_terminal_just_because_attempted(tmp_path: Path) -> None:
    controller = VillaniModeController(
        SequencedRunner(tmp_path, [{"terminated_reason": "model_idle"}]),
        tmp_path,
        takeover_config=TakeoverConfig(max_waves=1),
    )
    controller.planner = SequencedPlanner([[_op("Task A")]])
    summary = controller.run()
    assert summary["tasks_attempted"][0]["status"] in {"retryable", "exhausted"}


def test_controller_rediscoveries_opportunities_each_wave(tmp_path: Path) -> None:
    runner = SequencedRunner(
        tmp_path, [{"inspection_summary": "x"}, {"inspection_summary": "x"}]
    )
    planner = SequencedPlanner(
        [
            [_op("Task A", TaskContract.INSPECTION.value)],
            [_op("Task B", TaskContract.INSPECTION.value)],
        ]
    )
    controller = VillaniModeController(
        runner, tmp_path, takeover_config=TakeoverConfig(max_waves=2)
    )
    controller.planner = planner
    controller.run()
    assert planner.calls >= 2


def test_partial_failure_generates_followup_task(tmp_path: Path) -> None:
    runner = SequencedRunner(
        tmp_path,
        [{"intentional_changes": ["src/app.py"], "terminated_reason": "completed"}],
    )
    controller = VillaniModeController(
        runner, tmp_path, takeover_config=TakeoverConfig(max_waves=1)
    )
    planner = SequencedPlanner([[_op("Bootstrap minimal tests")]])
    controller.planner = planner
    controller.run()
    assert any(
        "complete" in op.title.lower()
        for op in controller._followup_queue + controller._retryable_queue
    )


def test_post_edit_validation_followup_enqueued_after_changes(tmp_path: Path) -> None:
    runner = SequencedRunner(
        tmp_path, [{"intentional_changes": ["src/app.py"], "validation_artifacts": []}]
    )
    controller = VillaniModeController(
        runner, tmp_path, takeover_config=TakeoverConfig(max_waves=1)
    )
    controller.planner = SequencedPlanner([[_op("Bootstrap minimal tests")]])
    controller.run()
    assert any(
        op.title == "Validate recent autonomous changes"
        for op in controller._followup_queue
    )


def test_retry_budget_exhausts_task_lineage(tmp_path: Path) -> None:
    runner = SequencedRunner(tmp_path, [{"terminated_reason": "model_idle"}] * 4)
    controller = VillaniModeController(
        runner, tmp_path, takeover_config=TakeoverConfig(max_waves=4)
    )
    controller.planner = SequencedPlanner([[_op("Task A")]] * 4)
    summary = controller.run()
    statuses = [
        t["status"] for t in summary["tasks_attempted"] if t["title"] == "Task A"
    ]
    assert "exhausted" in statuses


def test_non_actionable_failure_retries_once_then_exhausts(tmp_path: Path) -> None:
    runner = SequencedRunner(tmp_path, [{"terminated_reason": "model_idle"}] * 3)
    controller = VillaniModeController(
        runner, tmp_path, takeover_config=TakeoverConfig(max_waves=3)
    )
    controller.planner = SequencedPlanner([[_op("Task A")]] * 3)
    summary = controller.run()
    assert summary["tasks_attempted"][-1]["status"] == "exhausted"


def test_stop_reason_not_triggered_by_attempted_titles_alone(tmp_path: Path) -> None:
    runner = SequencedRunner(
        tmp_path, [{"terminated_reason": "model_idle"}, {"inspection_summary": "ok"}]
    )
    controller = VillaniModeController(
        runner, tmp_path, takeover_config=TakeoverConfig(max_waves=2)
    )
    controller.planner = SequencedPlanner(
        [[_op("Task A", TaskContract.INSPECTION.value)]] * 2
    )
    summary = controller.run()
    assert (
        summary["done_reason"]
        != "No remaining opportunities above confidence threshold."
    )


def test_task_local_changed_files_excludes_preexisting_dirt(tmp_path: Path) -> None:
    import subprocess

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

    runner = SequencedRunner(
        tmp_path,
        [{"writes": [("src/new.py", "x=1\n")], "intentional_changes": ["src/new.py"]}],
    )
    controller = VillaniModeController(
        runner, tmp_path, takeover_config=TakeoverConfig(max_waves=1)
    )
    controller.planner = SequencedPlanner([[_op("Task A")]])
    summary = controller.run()
    assert summary["tasks_attempted"][0]["files_changed"] == ["src/new.py"]


def test_takeover_stops_when_all_remaining_work_is_terminal(tmp_path: Path) -> None:
    controller = VillaniModeController(
        SequencedRunner(tmp_path, []),
        tmp_path,
        takeover_config=TakeoverConfig(max_waves=1, min_confidence=0.9),
    )
    controller.planner = SequencedPlanner([[_op("Task A", confidence=0.6)]])
    summary = controller.run()
    assert summary["done_reason"].startswith(
        "No remaining opportunities above confidence threshold"
    )


def test_global_takeover_budget_limits_iterations(tmp_path: Path) -> None:
    runner = SequencedRunner(tmp_path, [{"inspection_summary": "ok"}] * 12)
    cfg = TakeoverConfig(max_waves=10, max_total_task_attempts=3, stagnation_cycle_limit=0)
    controller = VillaniModeController(runner, tmp_path, takeover_config=cfg)
    controller.planner = SequencedPlanner(
        [[_op(f"Task {i}", TaskContract.INSPECTION.value)] for i in range(10)]
    )
    summary = controller.run()
    assert summary["done_reason"] == "Villani mode budget exhausted."


def test_validation_followup_for_importability_example(tmp_path: Path) -> None:
    runner = SequencedRunner(
        tmp_path,
        [
            {
                "intentional_changes": ["src/__init__.py", "requirements.txt"],
                "validation_artifacts": [],
            }
        ],
    )
    controller = VillaniModeController(
        runner, tmp_path, takeover_config=TakeoverConfig(max_waves=1)
    )
    controller.planner = SequencedPlanner(
        [[_op("Validate baseline importability", TaskContract.VALIDATION.value)]]
    )
    controller.run()
    titles = [op.title for op in controller._followup_queue]
    assert any(
        "re-run validate baseline importability validation" in t.lower()
        or t == "Validate recent autonomous changes"
        for t in titles
    )
