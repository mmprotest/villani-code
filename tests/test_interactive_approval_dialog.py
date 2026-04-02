import asyncio
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, ListView, Static

from villani_code.tui.app import VillaniTUI, VillaniTranscript
from villani_code.tui.controller import RunnerController
from villani_code.tui.messages import ApprovalRequest
from villani_code.tui.widgets.approval import ApprovalBar


class DummyRunner:
    permissions = None
    model = "demo"


class DummyApp:
    def __init__(self):
        self.messages = []

    def post_message(self, message):
        self.messages.append(message)


class FakeController:
    def __init__(self) -> None:
        self.approvals: list[tuple[str, str]] = []
        self.prompts: list[str] = []

    def resolve_approval(self, request_id: str, choice: str) -> None:
        self.approvals.append((request_id, choice))

    def run_prompt(self, text: str) -> None:
        self.prompts.append(text)


def test_approval_bridge_blocks_worker_until_resolved(tmp_path: Path) -> None:
    controller = RunnerController(DummyRunner(), DummyApp())
    result = {}

    def worker() -> None:
        result["approved"] = controller.request_approval("Read", {"file_path": "README.md"})

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)
    req = next(m for m in controller.app.messages if m.__class__.__name__ == "ApprovalRequest")
    controller.resolve_approval(req.request_id, "yes")
    t.join(timeout=1)

    assert result["approved"] is True


def test_approval_bar_has_local_keybindings() -> None:
    keys = {binding.key for binding in ApprovalBar.BINDINGS}
    assert {"up", "down", "enter", "escape"}.issubset(keys)


def test_approval_shows_all_three_options(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        async with app.run_test() as pilot:
            bar = app.query_one(ApprovalBar)
            bar.show_request("Allow?", "r1", ["yes", "always", "no"])
            await pilot.pause()
            options = bar.query_one("#approval-options", ListView)
            assert len(options.children) == 3
            assert bar.display is True
            assert options.has_focus
            bar.hide_request()
            assert bar.display is False

    asyncio.run(run())






def test_approval_prompt_renders_literal_markup_like_text(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        async with app.run_test() as pilot:
            prompt = (
                'Allow Bash on cd "C:\\tmp" && echo "<div class=\"header\">" && '
                'echo "[bold]oops[/bold]" && echo "go.Bar("'
            )
            app.on_approval_request(ApprovalRequest(prompt, ["yes", "always", "no"], "r-markup"))
            await pilot.pause()

            bar = app.query_one(ApprovalBar)
            prompt_widget = bar.query_one("#approval-prompt", Static)

            assert bar.display is True
            assert str(prompt_widget.renderable) == prompt

    asyncio.run(run())
def test_stylesheet_loads_and_app_mounts(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#log", VillaniTranscript) is not None

    asyncio.run(run())
def test_approval_keys_are_contained_and_resolve_selection(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        app.controller = FakeController()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", Input)
            input_widget.value = "should-not-submit"
            input_widget.focus()

            app.on_approval_request(ApprovalRequest("Allow?", ["yes", "always", "no"], "r1"))
            bar = app.query_one(ApprovalBar)
            await pilot.pause()

            await pilot.press("down")
            await pilot.pause()
            assert bar.query_one("#approval-options", ListView).index == 1

            await pilot.press("enter")
            await pilot.pause()

            assert app.controller.approvals == [("r1", "always")]
            assert input_widget.disabled is False
            assert input_widget.has_focus
            assert app.controller.prompts == []

    asyncio.run(run())


def test_one_enter_resolves_selected_approval_choice_immediately(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        app.controller = FakeController()
        async with app.run_test() as pilot:
            app.on_approval_request(ApprovalRequest("Allow?", ["yes", "always", "no"], "r-enter"))
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()

            assert app.controller.approvals == [("r-enter", "yes")]
            assert app.query_one(ApprovalBar).display is False

    asyncio.run(run())


def test_ctrl_c_twice_posts_status_then_exits(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert str(app.query_one("#status-text", Static).render()) == (
                "Interrupted current session. Press Ctrl+C again to exit Villani Code."
            )
            assert app.is_running is True

            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app.is_running is False

    asyncio.run(run())


def test_enter_submits_normally_after_approval_resolution(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        app.controller = FakeController()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", Input)
            app.on_approval_request(ApprovalRequest("Allow?", ["yes", "always", "no"], "r-submit"))
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()
            assert app.controller.approvals == [("r-submit", "yes")]

            input_widget.value = "submit-now"
            await pilot.press("enter")
            await pilot.pause()

            assert app.controller.prompts == ["submit-now"]

    asyncio.run(run())


def test_escape_denies_and_restores_focus(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        app.controller = FakeController()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", Input)
            app.on_approval_request(ApprovalRequest("Allow?", ["yes", "always", "no"], "r2"))
            await pilot.pause()

            await pilot.press("escape")
            await pilot.pause()

            assert app.controller.approvals == [("r2", "no")]
            assert input_widget.disabled is False
            assert input_widget.has_focus

    asyncio.run(run())
