import asyncio
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Input

from villani_code.tui.app import VillaniTUI
from villani_code.tui.widgets.slash_popup import SlashCommandPopup


class DummyRunner:
    model = "demo"
    permissions = None


class FakeController:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_prompt(self, text: str) -> None:
        self.calls.append(text)

    def resolve_approval(self, request_id: str, choice: str) -> None:
        return None


def test_slash_commands_are_intercepted_and_unknown_is_local(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    app.controller = FakeController()

    app.on_input_submitted(Input.Submitted(Input(id="input"), "/help"))
    app.on_input_submitted(Input.Submitted(Input(id="input"), "/tasks"))
    app.on_input_submitted(Input.Submitted(Input(id="input"), "/wat"))

    assert app.controller.calls == []
    assert "Unknown command: /wat. Type /help for commands." in app._log_plain_text


def test_help_output_lists_supported_slash_commands(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    app.controller = FakeController()

    app.on_input_submitted(Input.Submitted(Input(id="input"), "/help"))

    for command in ("/help", "/tasks", "/settings", "/diff", "/rewind", "/export", "/fork"):
        assert command in app._log_plain_text


def test_normal_prompt_flow_still_calls_run_prompt(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    app.controller = FakeController()

    app.on_input_submitted(Input.Submitted(Input(id="input"), "hello"))

    assert app.controller.calls == ["hello"]


def test_slash_popup_visibility_and_filtering(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        app.controller = FakeController()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", Input)
            popup = app.query_one(SlashCommandPopup)

            input_widget.focus()
            await pilot.press("slash")
            await pilot.pause()
            assert popup.visible
            assert popup.has_items

            await pilot.press("h", "e")
            await pilot.pause()
            selected = popup.selected_item()
            assert selected is not None
            assert selected.trigger == "/help"

            input_widget.value = ""
            await pilot.pause()
            assert not popup.visible

            input_widget.value = "hello"
            await pilot.pause()
            assert not popup.visible

    asyncio.run(run())


def test_slash_popup_keyboard_controls(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        app.controller = FakeController()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", Input)
            popup = app.query_one(SlashCommandPopup)

            input_widget.focus()
            await pilot.press("slash")
            await pilot.pause()
            first = popup.selected_item()
            assert first is not None

            await pilot.press("down")
            await pilot.pause()
            second = popup.selected_item()
            assert second is not None
            assert second.trigger != first.trigger

            await pilot.press("up")
            await pilot.pause()
            assert popup.selected_item() is not None
            assert popup.selected_item().trigger == first.trigger

            await pilot.press("tab")
            await pilot.pause()
            assert input_widget.value == first.trigger
            assert popup.visible

            input_widget.value = "/"
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()
            assert app.controller.calls == []
            assert not popup.visible
            assert "not implemented yet in this build" in app._log_plain_text

            input_widget.value = "/"
            await pilot.pause()
            assert popup.visible
            await pilot.press("escape")
            await pilot.pause()
            assert not popup.visible

    asyncio.run(run())
