import asyncio
from pathlib import Path

from textual.widgets import Input

from villani_code.tui.app import VillaniTUI
from villani_code.tui.messages import LogAppend


class DummyRunner:
    model = "demo"
    permissions = None


class FakeController:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_prompt(self, text: str) -> None:
        self.calls.append(text)


def test_tui_constructs_with_runner(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    assert app.runner.model == "demo"


def test_tui_uses_textual_css_file(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    assert app.CSS_PATH == "styles.tcss"


def test_enter_submit_path_calls_controller_without_global_enter_binding(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    app.controller = FakeController()
    submitted = Input.Submitted(Input(id="input"), "hello")

    app.on_input_submitted(submitted)

    assert app.controller.calls == ["hello"]


def test_ai_stream_starts_on_fresh_line(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        async with app.run_test() as pilot:
            app.post_message(LogAppend("> hi", kind="user"))
            app.post_message(LogAppend("hello", kind="stream"))
            app.post_message(LogAppend(" world", kind="stream"))
            app.post_message(LogAppend("write out.py", kind="meta"))
            await pilot.pause()
            log = app.query_one("#log")
            lines = list(log.lines)
            rendered = "\n".join(lines)
            assert "> hihello" not in rendered
            assert "\nhello world" in rendered
            assert rendered.count("hello world") == 1

    asyncio.run(run())


def test_space_key_inserts_space_in_input(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", Input)
            input_widget.value = "hello"
            input_widget.cursor_position = len(input_widget.value)
            input_widget.focus()
            await pilot.press("space")
            await pilot.pause()
            assert input_widget.value == "hello "

    asyncio.run(run())
