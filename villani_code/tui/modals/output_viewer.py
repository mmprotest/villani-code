from __future__ import annotations

from prompt_toolkit.widgets import Frame, TextArea


class OutputViewerModal:
    def __init__(self) -> None:
        self.text = TextArea(read_only=True, scrollbar=True)
        self.container = Frame(self.text, title="Tool Output")

    def set_text(self, value: str) -> None:
        self.text.text = value
