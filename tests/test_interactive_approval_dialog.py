import asyncio
import threading
import time
from pathlib import Path

from textual.widgets import Input, ListView

from villani_code.tui.app import VillaniTUI
from villani_code.tui.controller import RunnerController
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


def test_approval_keys_are_contained_and_resolve_selection(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        app.controller = FakeController()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", Input)
            input_widget.value = "should-not-submit"
            input_widget.focus()

            bar = app.query_one(ApprovalBar)
            bar.show_request("Allow?", "r1", ["yes", "always", "no"])
            app.call_after_refresh(lambda: bar.query_one("#approval-options", ListView).focus())
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

            input_widget.value = "submit-now"
            await pilot.press("enter")
            await pilot.pause()
            assert app.controller.prompts == ["submit-now"]

    asyncio.run(run())
