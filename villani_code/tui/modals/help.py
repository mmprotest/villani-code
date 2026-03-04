from __future__ import annotations

from prompt_toolkit.widgets import Box, Frame, TextArea


def build_help_modal() -> Frame:
    text = """Shortcuts
Ctrl+P palette
Ctrl+S checkpoint
Ctrl+D diff panel
Ctrl+T task panel
Ctrl+F focus mode
Ctrl+O verbose output
Ctrl+/ help
Ctrl+E open full output
Ctrl+G toggle code fold
Esc close modal

Commands
/help /tasks /diff /settings /rewind /export /fork /jobs /kill
"""
    return Frame(Box(TextArea(text=text, read_only=True), padding=1), title="Shortcuts Help")
