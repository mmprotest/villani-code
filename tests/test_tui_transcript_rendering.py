from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Markdown, Static

from villani_code.tui.app import VillaniTUI
from villani_code.tui.messages import LogAppend


class DummyRunner:
    permissions = None
    model = "demo"


def test_plain_and_markdown_blocks_are_separate(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        async with app.run_test() as pilot:
            app.on_log_append(LogAppend("read  README.md", kind="meta"))
            app.on_log_append(LogAppend("### Heading\n\n- one\n- two", kind="ai"))
            await pilot.pause()

            plain_lines = app.query("Static.log-plain")
            markdown_blocks = app.query("Markdown.log-assistant-markdown")

            assert any("read  README.md" in str(widget.render()) for widget in plain_lines)
            assert len(markdown_blocks) >= 1
            assert isinstance(markdown_blocks.first(), Markdown)

    asyncio.run(run())


def test_streaming_finalizes_into_markdown_block(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        async with app.run_test() as pilot:
            app.on_log_append(LogAppend("Hello ", kind="stream"))
            app.on_log_append(LogAppend("**world**", kind="stream"))
            await pilot.pause()

            stream_widget = app.query_one("Static.log-assistant-stream", Static)
            assert "Hello **world**" in str(stream_widget.render())

            app.on_log_append(LogAppend("meta line", kind="meta"))
            await pilot.pause()

            assert len(app.query("Static.log-assistant-stream")) == 0
            assert len(app.query("Markdown.log-assistant-markdown")) >= 1
            assert app._log_plain_text.endswith("Hello **world**\nmeta line\n")

    asyncio.run(run())


def test_copy_console_uses_plain_text_transcript(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunner(), tmp_path)
        copied: dict[str, str] = {}

        def _capture(text: str) -> None:
            copied["value"] = text

        app._copy_to_clipboard = _capture  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            app.on_log_append(LogAppend("> hi", kind="user"))
            app.on_log_append(LogAppend("# Title\n\nParagraph", kind="ai"))
            await pilot.pause()
            app.action_copy_console()

        assert copied["value"].endswith("> hi\n# Title\n\nParagraph")

    asyncio.run(run())
