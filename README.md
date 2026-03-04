# Villani Code

Villani Code is a production-oriented terminal agent runner that connects to a compatible `/v1/messages` backend and executes a secure tool loop with streaming output.

## Quickstart

```bash
pip install -e .
villani-code run "summarize this repo" --base-url http://localhost:8000 --model local-model
```

Interactive mode:

```bash
villani-code interactive --base-url http://localhost:8000 --model local-model
```

Run with `--help` to view all available commands:

```bash
villani-code --help
```

## Key features

- Interactive REPL with slash commands, history, and `!` bash mode.
- Permission engine (`deny -> ask -> allow`) with Bash ASK-by-default and optional `BashSafe` auto-approval for safe read/build/test commands.
- First-class edit proposals with `/propose`, `/edits`, `/show <id>`, `/apply <id>`, `/reject <id>`.
- Checkpoints and rewind stored under `.villani_code/checkpoints/`.
- Sessions and transcript persistence under `.villani_code/`.
- Skills from `.villani/skills/**/SKILL.md`.
- Subagents from `.villani/agents/*.{json,yaml}` plus built-ins.
- MCP config loading from project/user/local/managed scopes.
- Hooks for tool lifecycle events.
- Local plugin install/list/remove.

## Migration notes

The previous minimal runner only supported a basic loop with `Ls/Read/Grep/Bash/Write/Patch`. This version adds interactive workflows, permissions, checkpoints, hooks, extensibility, and more built-in tools while preserving compatibility with `/v1/messages` request/streaming semantics.

See `docs/` for configuration details.

## Development

Set up a local development environment and run tests:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
```

Useful docs:

- `docs/settings.md` for configuration.
- `docs/permissions.md` for sandbox and approval behavior.
- `docs/skills.md` for skill discovery and loading.
