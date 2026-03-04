# Villani Code UI Components

The interactive terminal uses a prompt_toolkit full-screen Application.

## Persistent layout
- Transcript pane at top with scrolling.
- Input bar below transcript.
- Status bar on the bottom row.

## Panels
- Task Board panel can be shown and hidden.
- Diff Viewer panel can be shown and hidden.
- In narrow terminals, panels can render below transcript.

## Modals
- Command Palette with fuzzy matching and live results.
- Shortcuts Help overlay.
- Settings overlay with runtime toggles.
- Tool Output viewer for expanded output content.

## Transcript behavior
- User, assistant, tool call, and tool result entries are rendered inside transcript.
- Long fenced code blocks are folded with toggle support.
- Tool output stores preview in transcript and full content in an expandable modal.
