# Villani Code

Villani Code is a **disciplined, small-model-aware coding agent** with an interactive terminal experience as the flagship workflow.

It is built for constrained inference environments (local, quantized, or weaker models) where reliability comes from explicit control loops rather than hidden magic.

## Product focus

Villani keeps coding work narrow, inspectable, and recoverable:
- explicit planning before edits
- visible context governance under tight budgets
- scoped validation and bounded repair
- transparent transcripts and task outcomes

## Installation

### 1) Recommended for most users (interactive UI)
```bash
pip install .[tui]
```

### 2) Full install (UI + all optional extras)
```bash
pip install .[all]
```

### 3) Lean/core install (automation, CI, minimal environments)
```bash
pip install .
```

### 4) Development tooling
```bash
pip install .[dev]
```

Install-tier behavior:

- `.[tui]` (recommended): full interactive experience (`interactive`, `villani-mode`, default launch path).
- base install (`.`): headless/automation commands (`init`, `run`, `context`, `eval`, benchmark/reporting).
- if TUI extras are missing and interactive mode is requested, Villani prints: `Interactive mode requires the optional TUI dependencies. Install with: pip install .[tui]`.

## Architecture (alpha, pragmatic)

- `villani_code.cli`: Typer CLI entry points; interactive mode is still the default UX path.
- `villani_code.interactive` + `villani_code.tui.*`: Textual application shell, loaded lazily when interactive commands are invoked.
- `villani_code.state.Runner`: core runtime loop shared by interactive and headless execution paths.
- `villani_code.state_runtime` / `villani_code.state_tooling` / `villani_code.state_execution`: focused runtime helpers for message prep, tool policy, and execution summarization.
- `villani_code.autonomous` + `villani_code.autonomous_helpers` + `villani_code.autonomous_progress`: bounded Villani-mode planning, progress tracking, and stop/retry governance.
- `villani_code.context_governance`: context inventory, pruning, checkpointing, and reset behavior.

## Core vs UI dependency boundary

- The interactive Textual UI is the **primary** product experience.
- Core runtime and headless commands are intentionally decoupled from eager Textual imports, so CI/automation paths remain reliable.
- Interactive commands (`interactive`, `villani-mode`, default no-subcommand path) load TUI dependencies lazily and fail with a clear install hint when missing.
- Lean installs exist for testing and automation, but the recommended path for normal users remains the UI-enabled install.

## Typical workflow

1. Initialize repo memory (`villani-code init`).
2. Use interactive mode (`villani-code interactive ...`), or default launch with no subcommand when base config is provided.
3. Run narrow tasks (`villani-code run ...`) when scripting or automating.
4. Review context pressure (`villani-code context --json`).
5. Checkpoint and reset when stale (`checkpoint` / `reset-from-checkpoint`).

## CLI examples

```bash
villani-code interactive --base-url http://localhost:8000 --model local-model
villani-code run "fix failing test in tests/test_parser.py" --base-url http://localhost:8000 --model local-model
villani-code context --json
villani-code eval --suite tests/fixtures/eval/suite.json --json
```

## Benchmark mode (agent-layer benchmark)

`villani-code benchmark` compares coding **agents** (not model families) under fixed tasks, repo snapshot, and objective validation checks.

Fairness is now explicit per run:

- `same-backend`: every participating adapter is proven to use the same OpenAI-compatible backend + model identifier.
- `native-cli`: single-agent/default-provider run.
- `mixed`: heterogeneous setup (exploratory, not apples-to-apples headline fairness).

If configuration cannot prove same-backend parity, reports are marked `mixed` and include a fairness warning.

### Run Villani-only benchmark

```bash
villani-code benchmark \
  --tasks-dir benchmark_tasks/villani_code \
  --agent villani \
  --repo . \
  --base-url http://localhost:8000 \
  --model your-model
```

### Compare multiple agents

```bash
villani-code benchmark \
  --tasks-dir benchmark_tasks/villani_code \
  --agent villani \
  --agent claude-code \
  --agent opencode \
  --agent copilot-cli \
  --repo . \
  --base-url http://localhost:8000 \
  --model your-model
```

### Outputs

Each benchmark run writes:
- `benchmark_results.json`
- `benchmark_results.md`
- `benchmark_results.csv`
- per-run artifacts (`stdout.txt`, `stderr.txt`, `git_diff.patch`, `changed_files.json`, `validation_results.json`, `metadata.json`)

### Current external-adapter limitations

External adapters are CLI-driven and intentionally conservative. If an executable is missing, auth is not configured, or unattended mode is not supported on that machine/version, the run is reported as **skipped** (not as success, and distinct from failures).
