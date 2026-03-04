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

## Prompt caching and compression
- System prompt blocks now place an ephemeral cache checkpoint on the final stable system block.
- The static reminders block in the first user message also carries an ephemeral cache checkpoint.
- Tool schemas are sanitized to a minimal JSON schema to reduce request token usage.
- Before each model call, old conversation turns are compressed when prompt size exceeds the configured max prompt chars value.
- Use `--max-prompt-chars` to control the compression threshold.

## Read tool windowing
- Read supports `offset_lines` and `limit_lines` to fetch a line window rather than entire files.
- Read output is line-numbered and truncated once it exceeds `max_bytes`.
- When truncated, output includes a hint to request the next window with `offset_lines` and `limit_lines`.
