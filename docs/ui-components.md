# Villani Code UI Components

## Overview

The interactive terminal UX is implemented under `villani_code/tui/` (Textual app + lightweight component models).

Behavioral guarantees:

- single main transcript/output pane (no split secondary log).
- vertical scrolling with wrapped lines (no horizontal-scroll workflow).
- assistant output streams inline in the same pane.
- approval prompts render inline with visible choices and single-enter confirmation.
- durable progress events (including read/write/patch file activity) persist in transcript.

## Components

- `villani_code/tui/app.py`: interactive Textual application shell.
- `villani_code/tui/controller.py`: runner ↔ UI event bridge and approval flow.
- `villani_code/tui/components/status_bar.py`: compact bottom status model and formatter.
- `villani_code/tui/components/command_palette.py`: fuzzy command lookup and action dispatch.
- `villani_code/tui/components/task_board.py`: task and timeline model for async and long operations.
- `villani_code/tui/components/diff_viewer.py`: parsed git diff with folding and annotations.
- `villani_code/tui/components/settings.py`: user and project settings with precedence and hot reload polling.

## ASCII mockup

```text
┌ Conversation ─────────────────────────────────────────────────────────────┐
│ user: /diff                                                               │
│ assistant: showing enhanced diff view                                     │
└────────────────────────────────────────────────────────────────────────────┘
🤖 Villani Code > _
net:connected/1s | tok:482 (90/m) | tools:0:- | settings:Ctrl+P
```
