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
- Animated bottom-toolbar status spinner in interactive mode, including current tool/detail context.
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

## Small model mode

Use `--small-model` to enable runner-side support for local/smaller models:

- Persistent repository index at `.villani_code/index/index.json` with language/symbol/snippet metadata.
- Retrieval briefing injected before each turn with top relevant files and reasons.
- Deterministic repo map added to the system prompt.
- Deterministic context compaction with a hard character budget.
- Conservative edit safeguards (read-before-edit and patch-first behavior for large files).
- Automatic post-edit verification (`git diff --stat`, `git diff`, and lightweight language checks).

Example:

```bash
villani-code interactive --base-url http://localhost:8000 --model local-model --small-model
```

Recommended for roughly 3B-14B local models where prompt budget and planning depth are limited. Tradeoff: stricter edits and compacted tool output can reduce flexibility but increases stability.


## TUI key behavior

- Approval prompts are inline: use `Up` / `Down` to move, `Enter` to confirm, and `Esc` to cancel.
- Log scrolling supports wheel modifiers: normal wheel (medium), `Shift+wheel` (large), `Ctrl+wheel` (very large).
- While streaming, status shows `FOLLOW` when auto-tail is active and `PAUSED` when you scroll away from the tail.

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
