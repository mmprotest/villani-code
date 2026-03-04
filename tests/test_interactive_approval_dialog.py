import asyncio
import threading
import time
from pathlib import Path

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
            options = bar.query_one("#approval-options")
            assert len(options.options) == 3
            assert int(options.styles.height.value) == 3

    asyncio.run(run())
