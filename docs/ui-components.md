# Villani Code UI Components

## Overview

The interactive terminal UX is built with prompt_toolkit and rich.

## Components

- `ui/status_bar.py`: compact bottom status with connectivity, tokens, tools, and settings hint.
- `ui/command_palette.py`: fuzzy command lookup and action dispatch.
- `ui/task_board.py`: task and timeline model for async and long operations.
- `ui/diff_viewer.py`: parsed git diff with folding and annotations.
- `ui/settings.py`: user and project settings with precedence and hot reload polling.
- `ui/themes.py`: prompt_toolkit and rich style mapping.

## ASCII mockup

```text
┌ Conversation ─────────────────────────────────────────────────────────────┐
│ user: /diff                                                               │
│ assistant: showing enhanced diff view                                     │
└────────────────────────────────────────────────────────────────────────────┘
🤖 Villani Code > _
net:connected/1s | tok:482 (90/m) | tools:0:- | settings:Ctrl+P
```
