from pathlib import Path

from ui.task_board import TaskManager
from villani_code.tui.app import TranscriptView
from villani_code.tui.controller import Controller
from villani_code.tui.state import UIState


class DummyCheckpoints:
    def create(self, *_args, **_kwargs):
        return None

    def list(self):
        return []


class DummyRunner:
    checkpoints = DummyCheckpoints()

    def run(self, _text, _messages=None):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}, "messages": []}


def test_controller_routes_commands(tmp_path: Path) -> None:
    state = UIState()
    transcript = TranscriptView()
    controller = Controller(DummyRunner(), tmp_path, state, transcript, TaskManager())

    import asyncio

    asyncio.run(controller.handle_command("/tasks"))
    assert state.show_tasks is True
    asyncio.run(controller.handle_command("/diff"))
    assert state.show_diff is True
    asyncio.run(controller.handle_command("/settings"))
    assert "Settings" in state.last_error
