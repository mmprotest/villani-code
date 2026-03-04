from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess


@dataclass(slots=True)
class DiffHunk:
    header: str
    lines: list[str]
    note: str | None = None
    folded: bool = False


@dataclass(slots=True)
class DiffFile:
    path: str
    hunks: list[DiffHunk] = field(default_factory=list)


class DiffViewer:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.annotations: dict[str, str] = {}
        self.side_by_side = False

    def load_diff(self) -> str:
        proc = subprocess.run(["git", "diff"], cwd=str(self.repo), text=True, capture_output=True)
        return proc.stdout

    def parse(self, diff_text: str) -> list[DiffFile]:
        files: list[DiffFile] = []
        current_file: DiffFile | None = None
        current_hunk: DiffHunk | None = None
        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                if current_file:
                    files.append(current_file)
                right = line.split(" b/")[-1]
                current_file = DiffFile(path=right)
                current_hunk = None
            elif line.startswith("@@") and current_file:
                current_hunk = DiffHunk(header=line, lines=[])
                current_file.hunks.append(current_hunk)
            elif current_hunk is not None:
                current_hunk.lines.append(line)
        if current_file:
            files.append(current_file)
        return files

    def fold_hunk(self, hunk: DiffHunk, context_lines: int = 4) -> DiffHunk:
        if len(hunk.lines) <= context_lines * 2:
            return hunk
        top = hunk.lines[:context_lines]
        bottom = hunk.lines[-context_lines:]
        hidden = len(hunk.lines) - len(top) - len(bottom)
        hunk.lines = top + [f"... {hidden} unchanged lines folded ..."] + bottom
        hunk.folded = True
        return hunk

    def annotate(self, file_path: str, hunk_header: str, note: str) -> None:
        self.annotations[f"{file_path}:{hunk_header}"] = note

    def render_plain(self, files: list[DiffFile]) -> str:
        chunks: list[str] = []
        for dfile in files:
            chunks.append(f"# {dfile.path}")
            for hunk in dfile.hunks:
                chunks.append(hunk.header)
                chunks.extend(_colorize_line(line) for line in hunk.lines)
        return "\n".join(chunks)


def _colorize_line(line: str) -> str:
    if line.startswith("+") and not line.startswith("+++"):
        return f"[green]{line}[/green]"
    if line.startswith("-") and not line.startswith("---"):
        return f"[red]{line}[/red]"
    return line
