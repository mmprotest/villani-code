# Villani Code

Villani Code is a terminal coding agent for **small local models**.

Default behavior is intentionally conservative:

- **local-safe preset (default)**
- strict planning and approvals for risky actions
- checkpointing before risky edit batches
- validation after edits
- durable transcripts for audit and replay

The core loop is:

`plan -> approval -> execute -> checkpoint -> validate -> review`

## Why this product exists

Small local models can be useful if the runtime is strict.
Villani Code focuses on:

- bounded autonomy
- safe repository changes
- explicit approvals for risky operations
- transparent transcripts and checkpoint recovery

## Presets

- `local-safe` (default): strict, bounded, fail-closed behavior for weak models.
- `local-fast`: local-first behavior with lighter constraints.
- `cloud-power`: explicit opt-in for broader behavior.

## Install

```bash
pip install .[tui]
```

## Quickstart

Interactive mode (default local-safe):

```bash
villani-code interactive --base-url http://127.0.0.1:1234 --model your-local-model
```

One-shot run (default local-safe):

```bash
villani-code run "Fix retry handling in the API client and update tests" --base-url http://127.0.0.1:1234 --model your-local-model
```

Opt into faster local behavior:

```bash
villani-code run "..." --preset local-fast --base-url http://127.0.0.1:1234 --model your-local-model
```

Opt into cloud-power behavior explicitly:

```bash
villani-code run "..." --preset cloud-power --no-small-model --base-url https://api.example.com --model frontier-model
```

## Safety defaults

By default, approval is required for risky operations including:

- dependency installation commands
- destructive shell commands
- network access
- file writes/patches
- git-destructive operations

Villani Code also enforces bounded execution budgets and context budgets in local-safe mode.

## Recovery and audit

- Checkpoints are created before risky edit batches.
- Roll back quickly:

```bash
villani-code rollback --repo /path/to/repo
```

- Transcripts are stored in `.villani_code/transcripts/` with durable events for plan, approvals, edits, validation, checkpoints, and outcome.

## Benchmarking

Benchmark reporting includes a safe-local framing and trust metrics (restraint + auditability), not just success rate.

## Development checks

```bash
python -m pytest
ruff check .
mypy villani_code
```
