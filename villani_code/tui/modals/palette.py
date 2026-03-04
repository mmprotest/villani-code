from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame, TextArea

from ui.command_palette import CommandAction, CommandPalette


class PaletteModal:
    def __init__(self, palette: CommandPalette, on_select: Callable[[CommandAction], None]) -> None:
        self.palette = palette
        self.on_select = on_select
        self.query_area = TextArea(height=1, prompt="Palette > ")
        self.query_area.buffer.on_text_changed += lambda _ : self.refresh()
        self.selection = 0
        self.results: list[tuple[int, object]] = []
        self.results_control = FormattedTextControl(self._render_results)
        self.container = Frame(HSplit([self.query_area, Window(height=10, content=self.results_control)]), title="Command Palette")
        self.refresh()

    def focus_target(self):
        return self.query_area

    def refresh(self) -> None:
        self.results = self.palette.search(self.query_area.text, limit=12)
        if self.selection >= len(self.results):
            self.selection = max(0, len(self.results) - 1)

    def move(self, delta: int) -> None:
        if not self.results:
            return
        self.selection = max(0, min(len(self.results) - 1, self.selection + delta))

    def submit(self) -> None:
        if not self.results:
            return
        self.on_select(self.results[self.selection][1].action)

    def _render_results(self):
        out = []
        for idx, (_score, item) in enumerate(self.results):
            marker = "> " if idx == self.selection else "  "
            out.append(("class:palette.selected" if idx == self.selection else "", f"{marker}{item.trigger} - {item.description}\n"))
        return out
