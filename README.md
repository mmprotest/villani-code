# Villani Code

Villani Code is a terminal-first coding agent for local repositories. It focuses on bounded execution, explicit permissions, and auditable output rather than autonomous behavior by default.

## What it does

- Reads and edits files in a repository.
- Runs shell commands under a configurable permission policy.
- Streams model output and tool activity in the terminal.
- Supports interactive and one-shot workflows.
- Includes benchmark tooling for repeatable task-based evaluation.

## What it does **not** guarantee

- It can still make incorrect code changes.
- Passing a command or test step is not proof of overall correctness.
- Safety controls reduce risk, but do not eliminate it.
- Results depend on model quality, prompt quality, and repository state.

Use version control, review diffs, and verify changes before merging.

## Installation

```bash
pip install .[tui]    # interactive TUI
pip install .         # headless CLI
pip install .[dev]    # development dependencies
```

## Quickstart

Interactive session:

```bash
villani-code interactive --base-url http://127.0.0.1:1234 --model your-model --repo /path/to/repo
```

One-shot task:

```bash
villani-code run "Add retry handling to API client and update tests." --base-url http://127.0.0.1:1234 --model your-model --repo /path/to/repo
```

Bounded autonomous pass:

```bash
villani-code --villani-mode --base-url http://127.0.0.1:1234 --model your-model --repo /path/to/repo
```

## Modes

- **Interactive**: default operator workflow with streaming output and inline approvals.
- **Run**: single instruction execution with direct output.
- **Villani mode**: bounded multi-step improvement loop with stop reasons.

## Safety and controls

- Permission controls for shell and file operations.
- Context governance and checkpointing support.
- Optional runtime hardening checks.
- Structured event output for post-run inspection.

Read:
- `docs/permissions.md`
- `docs/checkpointing.md`
- `docs/settings.md`

## Development checks

```bash
python -m pytest
ruff check .
mypy villani_code
```

If you modify autonomy, permissions, or benchmark behavior, run the relevant test subsets in addition to the full suite.
