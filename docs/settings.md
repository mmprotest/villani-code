# Settings

Project settings can be stored in `.villani/settings.json`.

Supported keys:
- `hooks`: event map for SessionStart, PreToolUse, PostToolUse, PreEdit, PostEdit, SessionEnd.
- `permissions`: deny/ask/allow lists.
- `mcp`: local MCP choices.

Permission modes:
- Normal
- Auto-accept edits
- Plan mode
- Dangerously skip permissions (CLI flag)

- `villani_mode`: bool (default `false`). If true, default startup uses autonomous Villani mode unless overridden by CLI flags.

CLI flags override settings, including `--villani-mode/--no-villani-mode`.
