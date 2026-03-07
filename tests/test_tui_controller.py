from __future__ import annotations

import pytest

pytest.importorskip("textual")

from villani_code.tui.controller import RunnerController
from villani_code.tui.messages import LogAppend, SpinnerState, StatusUpdate


class DummyApp:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def post_message(self, message: object) -> object:
        self.messages.append(message)
        return message


class ExplodingRunner:
    def __init__(self) -> None:
        self.print_stream = True
        self.approval_callback = None
        self.event_callback = None

    def run_villani_mode(self):
        raise RuntimeError("boom")


def test_tui_worker_logs_exception() -> None:
    app = DummyApp()
    controller = RunnerController(ExplodingRunner(), app)

    controller._run_villani_mode_worker()

    logs = [m for m in app.messages if isinstance(m, LogAppend)]
    assert any("ERROR RuntimeError: boom" in m.text for m in logs)
    assert any("Traceback" in m.text for m in logs)
    assert any(isinstance(m, SpinnerState) and m.active is False for m in app.messages)
    assert any(isinstance(m, StatusUpdate) and m.text == "Idle" for m in app.messages)
