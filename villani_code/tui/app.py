from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import count
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout import ConditionalContainer, Dimension, Float, FloatContainer, FormattedTextControl, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import Frame, TextArea

from ui.status_bar import StatusBar

from villani_code.tui.state import ActiveModal, UIState

_CODE_BLOCK = re.compile(r"```(?P<lang>\w+)?\n(?P<body>.*?)```", re.DOTALL)


@dataclass(slots=True)
class TranscriptItem:
    id: int
    kind: str
    text: str


class TranscriptView:
    def __init__(self) -> None:
        self._items: list[TranscriptItem] = []
        self._next_id = count(1)
        self._folded: dict[int, bool] = {}
        self._tool_output_full: dict[str, str] = {}
        self._selected_foldable: int | None = None
        self.control = FormattedTextControl(self._get_text)
        self.window = Window(content=self.control, wrap_lines=True, always_hide_cursor=True, right_margins=[], scroll_offsets=None)

    def append_user(self, text: str) -> None:
        self._items.append(TranscriptItem(next(self._next_id), "user", text))

    def append_assistant_delta(self, text_delta: str) -> None:
        if self._items and self._items[-1].kind == "assistant-delta":
            self._items[-1].text += text_delta
        else:
            self._items.append(TranscriptItem(next(self._next_id), "assistant-delta", text_delta))

    def finalize_assistant_message(self, full_text: str, blocks: list[dict[str, Any]]) -> None:
        if self._items and self._items[-1].kind == "assistant-delta":
            self._items.pop()
        item = TranscriptItem(next(self._next_id), "assistant", full_text)
        self._items.append(item)
        if _has_large_code_block(full_text):
            self._folded[item.id] = True
            self._selected_foldable = item.id

    def append_tool_call(self, name: str, input_dict: dict[str, Any]) -> None:
        self._items.append(TranscriptItem(next(self._next_id), "tool-call", f"{name} {input_dict}"))

    def append_tool_result(self, name: str, preview_text: str, full_text_id: str) -> None:
        self._tool_output_full[full_text_id] = preview_text if full_text_id not in self._tool_output_full else self._tool_output_full[full_text_id]
        self._items.append(TranscriptItem(next(self._next_id), "tool-result", f"{name}: {preview_text} [Expand:{full_text_id}]"))

    def store_full_output(self, output_id: str, text: str) -> None:
        self._tool_output_full[output_id] = text

    def get_full_output(self, output_id: str | None) -> str:
        if not output_id:
            return ""
        return self._tool_output_full.get(output_id, "")

    def toggle_selected_fold(self) -> None:
        if self._selected_foldable is None:
            return
        self._folded[self._selected_foldable] = not self._folded.get(self._selected_foldable, False)

    def _get_text(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        for item in self._items:
            style = {
                "user": "class:transcript.user",
                "assistant": "class:transcript.assistant",
                "assistant-delta": "class:transcript.assistant",
                "tool-call": "class:transcript.tool_call",
                "tool-result": "class:transcript.tool_result",
            }.get(item.kind, "")
            rendered = item.text
            if item.kind in {"assistant", "assistant-delta"} and self._folded.get(item.id, False):
                rendered = _fold_large_code_blocks(rendered)
            prefix = {
                "user": "\nYou> ",
                "assistant": "\nAssistant> ",
                "assistant-delta": "\nAssistant> ",
                "tool-call": "\nTool call> ",
                "tool-result": "\nTool result> ",
            }.get(item.kind, "\n")
            out.append(("class:transcript.prefix", prefix))
            out.append((style, rendered))
            out.append(("", "\n"))
        return out


class InputBar:
    def __init__(self, completer: Any) -> None:
        self.textarea = TextArea(height=1, prompt="Villani Code > ", multiline=False, completer=completer)


class TUIApp:
    def __init__(
        self,
        state: UIState,
        transcript: TranscriptView,
        input_bar: InputBar,
        status_bar: StatusBar,
        key_bindings: Any,
        right_panel: Any,
        bottom_panel: Any,
        palette_modal: Any,
        help_modal: Any,
        settings_modal: Any,
        output_modal: Any,
        style: Any,
    ) -> None:
        self.state = state
        self.transcript = transcript
        self.input_bar = input_bar
        self.status_bar = status_bar
        self.right_panel = right_panel
        self.bottom_panel = bottom_panel
        self.app = Application(layout=Layout(self._build_container(palette_modal, help_modal, settings_modal, output_modal)), key_bindings=key_bindings, full_screen=True, style=style)

    def _build_container(self, palette_modal: Any, help_modal: Any, settings_modal: Any, output_modal: Any):
        @Condition
        def show_side_panels() -> bool:
            return (self.state.show_tasks or self.state.show_diff) and not self.state.focus_mode

        @Condition
        def show_palette_modal() -> bool:
            return self.state.active_modal == ActiveModal.PALETTE

        @Condition
        def show_help_modal() -> bool:
            return self.state.active_modal == ActiveModal.HELP

        @Condition
        def show_settings_modal() -> bool:
            return self.state.active_modal == ActiveModal.SETTINGS

        @Condition
        def show_output_modal() -> bool:
            return self.state.active_modal == ActiveModal.OUTPUT

        transcript_frame = Frame(self.transcript.window, title="Transcript")
        body = VSplit(
            [
                transcript_frame,
                ConditionalContainer(self.right_panel, filter=show_side_panels),
            ]
        )
        bottom_panels = ConditionalContainer(self.bottom_panel, filter=show_side_panels)
        content = HSplit(
            [
                body,
                bottom_panels,
                Frame(self.input_bar.textarea, title="Input", height=Dimension.exact(3)),
                Window(height=1, content=FormattedTextControl(lambda: self.status_bar.format(get_app().output.get_size().columns if get_app() else 120)), style="class:bottom-toolbar"),
            ]
        )
        floats = [
            Float(content=ConditionalContainer(palette_modal, filter=show_palette_modal)),
            Float(content=ConditionalContainer(help_modal, filter=show_help_modal)),
            Float(content=ConditionalContainer(settings_modal, filter=show_settings_modal)),
            Float(content=ConditionalContainer(output_modal, filter=show_output_modal)),
        ]
        return FloatContainer(content=content, floats=floats)

    async def run(self) -> None:
        await self.app.run_async()

    def invalidate(self) -> None:
        self.app.invalidate()


def _has_large_code_block(text: str, threshold: int = 12) -> bool:
    for match in _CODE_BLOCK.finditer(text):
        if len(match.group("body").splitlines()) > threshold:
            return True
    return False


def _fold_large_code_blocks(text: str, threshold: int = 12, keep: int = 5) -> str:
    def _replace(match: re.Match[str]) -> str:
        lang = match.group("lang") or "text"
        lines = match.group("body").splitlines()
        if len(lines) <= threshold:
            return match.group(0)
        head = "\n".join(lines[:keep])
        return f"```{lang}\n{head}\n... folded ...\n```"

    return _CODE_BLOCK.sub(_replace, text)
