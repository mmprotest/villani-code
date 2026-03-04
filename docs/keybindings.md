# Villani Code Keybindings

## Global
- `Ctrl+P`: Open command palette overlay.
- `Ctrl+S`: Create checkpoint.
- `Ctrl+D`: Toggle diff viewer panel.
- `Ctrl+T`: Toggle task board panel.
- `Ctrl+F`: Focus mode toggle.
- `Ctrl+O`: Toggle verbose tool output.
- `Ctrl+/`: Open shortcuts help overlay.
- `Ctrl+C`: First press shows a 2 second warning. Press again within that window to exit cleanly.
- `Esc`: Close the active modal. In approval modal this denies the request.

## Transcript and tool output
- `Ctrl+G`: Toggle fold for selected long code block.
- `Ctrl+E`: Open full output overlay for selected tool result.

## Approvals
- `Enter`: Yes once.
- `Esc`: No.
- `Ctrl+B`: Run in background.

## Session resume
- Start a resumed interactive session with:
  - `villani-code interactive --resume <session_id> --base-url ... --model ...`

## Diff viewer panel
- `Up` and `Down`: Move selected file.
- `Enter`: Toggle folded hunk content.
- `s`: Toggle side by side mode.
- `Tab`: Switch pane focus.
- `a`: Add annotation to selected hunk.
