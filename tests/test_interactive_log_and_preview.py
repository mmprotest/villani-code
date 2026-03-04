from pathlib import Path

from villani_code.tui.controller import RunnerController
from villani_code.tui.messages import LogAppend


class DummyRunner:
    permissions = None


class DummyApp:
    def __init__(self):
        self.messages = []

    def post_message(self, message):
        self.messages.append(message)


def test_stream_event_posts_log_append_incrementally(tmp_path: Path) -> None:
    app = DummyApp()
    controller = RunnerController(DummyRunner(), app)

    controller.on_runner_event({"type": "stream_text", "text": "hel"})
    controller.on_runner_event({"type": "stream_text", "text": "lo"})

    chunks = [m.text for m in app.messages if isinstance(m, LogAppend)]
    assert chunks == ["hel", "lo"]
