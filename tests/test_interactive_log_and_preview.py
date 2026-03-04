from pathlib import Path

from prompt_toolkit.layout.margins import ScrollbarMargin

from villani_code.interactive import InteractiveShell


class DummyCheckpoints:
    def create(self, *_args, **_kwargs):
        return None

    def list(self):
        return []


class DummyPermissions:
    def target_for(self, tool: str, payload: dict) -> str:
        return f"{tool}:{payload.get('file_path', '<none>')}"


class DummyRunner:
    checkpoints = DummyCheckpoints()
    permissions = DummyPermissions()

    def run(self, _text):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


def test_startup_banner_appends_ascii_art_and_model_to_log(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    shell._append_startup_banner()
    log_text = shell.log_area.buffer.text

    assert "villani-fying your terminal" in log_text
    assert "Model:" in log_text


def test_append_log_uses_incremental_buffer_update(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)

    shell._append_log("first")
    first_text = shell.log_area.buffer.text
    shell._append_log("second")

    assert first_text == "first"
    assert shell.log_area.buffer.text.endswith("\nsecond")
    assert "\n".join(shell.log_lines) == shell.log_area.buffer.text


def test_log_uses_three_clickable_scrollbar_margins(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)

    margins = shell.log_area.window.right_margins
    assert len(margins) == 3
    assert all(isinstance(margin, ScrollbarMargin) for margin in margins)


def test_stream_deltas_append_inline_to_log(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)

    shell._append_log("you> hi")
    shell._append_stream_delta("hello")
    shell._append_stream_delta("\nworld")

    assert list(shell.log_lines)[-2:] == ["assistant> hello", "world"]


class StreamingRunner(DummyRunner):
    def __init__(self):
        self.event_callback = None

    def run(self, _text):
        self.event_callback({"type": "stream_text", "text": "final"})
        return {"response": {"content": [{"type": "text", "text": "final"}]}}


def test_streaming_response_is_not_duplicated_on_final_append(tmp_path: Path) -> None:
    shell = InteractiveShell(StreamingRunner(), tmp_path)

    shell._run_model_turn("hi")

    assert shell.log_area.text.count("assistant> final") == 1
