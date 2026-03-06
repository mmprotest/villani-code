from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ui.settings import SettingsManager
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
    (tmp_path / "tests" / "test_a.py").write_text("def test_ok():\n assert True\n", encoding="utf-8")
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
            AutonomousTask("a", "low", "", priority=0.1, confidence=0.1, verification_plan=["echo ok"]),
            AutonomousTask("b", "high", "", priority=0.9, confidence=0.9, verification_plan=["echo ok"]),
        ]
    )
    assert ranked[0].title == "high"


def test_done_state_when_no_worthwhile_candidates(tmp_path: Path) -> None:
    class NoWorkController(VillaniModeController):
        def generate_candidates(self, snapshot: RepoSnapshot):
            return [AutonomousTask("x", "speculative", "", priority=0.2, confidence=0.2, verification_plan=[])]

    controller = NoWorkController(StubRunner(tmp_path), tmp_path)
    summary = controller.run()
    assert "done_reason" in summary
    assert "confidence threshold" in summary["done_reason"]


def test_runner_villani_mode_auto_approves_edits(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=True)
    events: list[dict] = []
    runner.event_callback = events.append
    result = runner._execute_tool_with_policy("Write", {"file_path": "a.txt", "content": "x", "mkdirs": True}, "1", 0)
    assert result["is_error"] is False
    assert any(e.get("type") == "approval_auto_resolved" for e in events)


def test_hard_shell_denylist_still_active_in_villani_mode(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=True)
    result = runner._execute_tool_with_policy("Bash", {"command": "curl https://example.com", "cwd": ".", "timeout_sec": 5}, "1", 0)
    assert result["is_error"] is True


def test_cli_has_villani_mode_subcommand() -> None:
    cli_runner = CliRunner()
    result = cli_runner.invoke(cli.app, ["villani-mode", "--help"])
    assert result.exit_code == 0


def test_cli_has_takeover_subcommand() -> None:
    cli_runner = CliRunner()
    result = cli_runner.invoke(cli.app, ["takeover", "--help"])
    assert result.exit_code == 0


def test_cli_flag_overrides_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (home / ".villani").mkdir(parents=True)
    (home / ".villani" / "settings.json").write_text('{"villani_mode": true}', encoding="utf-8")

    settings = SettingsManager(repo, home=home).load()
    assert settings.villani_mode is True
    assert cli._resolve_villani_flag(repo, False) is False


def test_summary_generation_includes_verification(tmp_path: Path) -> None:
    task = AutonomousTask("1", "t", "r", priority=1.0, confidence=1.0, verification_plan=["echo ok"])
    task.status = "passed"
    task.verification_results = [{"command": "echo ok", "exit": 0}]
    summary_text = VillaniModeController.format_summary({"tasks_attempted": [{"title": task.title, "status": task.status, "verification": task.verification_results}], "done_reason": "done", "blockers": [], "files_changed": [], "recommended_next_steps": []})
    assert "verification" in summary_text


def test_villani_mode_uses_bounded_runner(tmp_path: Path) -> None:
    runner = StubRunner(tmp_path)
    controller = VillaniModeController(runner, tmp_path)

    task = AutonomousTask("1", "task", "because", priority=1.0, confidence=1.0, verification_plan=[])
    controller._execute_task(task)

    assert runner.last_budget is not None


def test_villani_mode_startup_without_prompt(tmp_path: Path) -> None:
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
