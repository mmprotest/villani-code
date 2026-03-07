from pathlib import Path

import pytest

pytest.importorskip("textual")

from villani_code.tui.controller import RunnerController
from villani_code.tui.messages import LogAppend, SpinnerState


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

    chunks = [m for m in app.messages if isinstance(m, LogAppend)]
    assert [(m.kind, m.text) for m in chunks] == [("stream", "hel"), ("stream", "lo")]


def test_model_started_uses_spinner_theme_label_not_literal_thinking(tmp_path: Path) -> None:
    app = DummyApp()
    controller = RunnerController(DummyRunner(), app)

    controller.on_runner_event({"type": "model_request_started"})

    state = next(m for m in app.messages if isinstance(m, SpinnerState))
    assert state.active is True
    assert state.label is None


def test_tool_started_logs_only_read_write_patch(tmp_path: Path) -> None:
    app = DummyApp()
    controller = RunnerController(DummyRunner(), app)

    controller.on_runner_event({"type": "tool_started", "name": "Read", "input": {"file_path": "a.py"}})
    controller.on_runner_event({"type": "tool_started", "name": "Write", "input": {"file_path": "b.py"}})
    controller.on_runner_event({"type": "tool_started", "name": "Patch", "input": {"file_path": "c.py"}})
    controller.on_runner_event({"type": "tool_started", "name": "Bash", "input": {"command": "echo hi"}})

    lines = [m.text for m in app.messages if isinstance(m, LogAppend)]
    assert lines == ["read  a.py", "write b.py", "patch c.py"]
    assert all("Using tool" not in line for line in lines)


def test_non_stream_fallback_has_no_assistant_prefix(tmp_path: Path) -> None:
    class NonStreamingRunner(DummyRunner):
        def run(self, _text):
            return {"response": {"content": [{"type": "text", "text": "hello"}]}}

    app = DummyApp()
    controller = RunnerController(NonStreamingRunner(), app)
    controller._run_prompt_worker("hi")

    ai = [m.text for m in app.messages if isinstance(m, LogAppend) and m.kind == "ai"]
    assert ai == ["hello"]
    assert "assistant>" not in ai[0]


def test_request_approval_posts_prompt_without_meta_spam(tmp_path: Path) -> None:
    app = DummyApp()
    controller = RunnerController(DummyRunner(), app)

    request_id = ""

    def resolve() -> None:
        nonlocal request_id
        import time

        deadline = time.time() + 1
        while time.time() < deadline:
            req = next((m for m in app.messages if m.__class__.__name__ == "ApprovalRequest"), None)
            if req is not None:
                request_id = req.request_id
                controller.resolve_approval(req.request_id, "yes")
                return
            time.sleep(0.01)

    import threading

    t = threading.Thread(target=resolve)
    t.start()
    approved = controller.request_approval("Read", {"file_path": "x.py"})
    t.join(timeout=1)

    assert approved is True
    lines = [m.text for m in app.messages if isinstance(m, LogAppend)]
    assert all("approval required" not in line for line in lines)
    assert request_id


def test_streamed_response_does_not_emit_fallback_ai_dump(tmp_path: Path) -> None:
    class StreamingRunner(DummyRunner):
        def run(self, _text):
            self.event_callback({"type": "stream_text", "text": "hello"})
            return {"response": {"content": [{"type": "text", "text": "hello"}]}}

    app = DummyApp()
    controller = RunnerController(StreamingRunner(), app)
    controller._run_prompt_worker("hi")

    stream = [m.text for m in app.messages if isinstance(m, LogAppend) and m.kind == "stream"]
    ai = [m.text for m in app.messages if isinstance(m, LogAppend) and m.kind == "ai"]
    assert stream == ["hello"]
    assert ai == []
