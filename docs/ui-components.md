# Villani Code UI Components

## Overview

The interactive terminal UX is implemented under `villani_code/tui/` (Textual app + lightweight component models).

## Components

- `villani_code/tui/app.py`: interactive Textual application shell.
- `villani_code/tui/controller.py`: runner ↔ UI event bridge and approval flow.
- `villani_code/tui/components/status_bar.py`: compact bottom status model and formatter.
- `villani_code/tui/components/command_palette.py`: fuzzy command lookup and action dispatch.
- `villani_code/tui/components/task_board.py`: task and timeline model for async and long operations.
- `villani_code/tui/components/diff_viewer.py`: parsed git diff with folding and annotations.
- `villani_code/tui/components/settings.py`: user and project settings with precedence and hot reload polling.
- `ui/`: compatibility shims that re-export `villani_code.tui.components.*` for legacy imports.

## ASCII mockup

```text
┌ Conversation ─────────────────────────────────────────────────────────────┐
│ user: /diff                                                               │
│ assistant: showing enhanced diff view                                     │
└────────────────────────────────────────────────────────────────────────────┘
🤖 Villani Code > _
net:connected/1s | tok:482 (90/m) | tools:0:- | settings:Ctrl+P
```
