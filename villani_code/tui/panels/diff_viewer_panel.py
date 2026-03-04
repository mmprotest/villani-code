from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame

from ui.diff_viewer import DiffViewer


@dataclass(slots=True)
class DiffModel:
    files: list[object] = field(default_factory=list)
    folded_regions: set[tuple[int, int]] = field(default_factory=set)


class DiffViewerPanel:
    def __init__(self, repo: Path) -> None:
        self.viewer = DiffViewer(repo)
        self.model = DiffModel()
        self.file_index = 0
        self.side_by_side = False
        self.file_control = FormattedTextControl(self._render_files)
        self.hunk_control = FormattedTextControl(self._render_hunks)
        self.container = Frame(VSplit([Window(content=self.file_control, width=28), Window(content=self.hunk_control)]), title="Diff Viewer")

    def load(self) -> None:
        parsed = self.viewer.parse(self.viewer.load_diff())
        self.model.files = parsed

    def move(self, delta: int) -> None:
        if not self.model.files:
            return
        self.file_index = max(0, min(len(self.model.files) - 1, self.file_index + delta))

    def toggle_side(self) -> None:
        self.side_by_side = not self.side_by_side

    def toggle_fold(self, hunk_index: int = 0) -> None:
        key = (self.file_index, hunk_index)
        if key in self.model.folded_regions:
            self.model.folded_regions.remove(key)
        else:
            self.model.folded_regions.add(key)

    def _render_files(self):
        rows = []
        for idx, dfile in enumerate(self.model.files):
            marker = ">" if idx == self.file_index else " "
            rows.append(("class:panel.selected" if idx == self.file_index else "", f"{marker} {dfile.path}\n"))
        return rows or [("", "No diff files\n")]

    def _render_hunks(self):
        if not self.model.files:
            return [("", "No diff loaded\n")]
        current = self.model.files[self.file_index]
        rows = []
        for hidx, hunk in enumerate(current.hunks):
            rows.append(("class:panel.header", hunk.header + "\n"))
            lines = hunk.lines
            if (self.file_index, hidx) in self.model.folded_regions and len(lines) > 6:
                lines = lines[:3] + ["... folded ..."] + lines[-3:]
            for line in lines:
                rows.append(("", line + "\n"))
        return rows
