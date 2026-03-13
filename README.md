# Villani Code

**The coding agent built for small local models.**

Villani Code is a terminal-first coding agent optimized for 7B to 14B local models on real codebases.

Most coding agents are built for strong hosted models and only incidentally support local ones. Villani Code is built for the opposite case: weak models, limited budgets, explicit permissions, and real repo work.

## Why

Small local models are cheap, private, and practical.

They are also easy to waste.

On weaker models, runtime design matters more:
- context selection
- task decomposition
- tool discipline
- validation loops
- token efficiency
- recovery from bad intermediate steps

Villani Code is designed to make small local models materially more useful for coding, not just demo-friendly.

## What Villani Code optimizes for

- **Small-model performance**  
  Built for local 7B to 14B models, not just adapted to them.

- **Useful diffs**  
  The goal is accepted changes, not impressive-looking agent runs.

- **Test-guided iteration**  
  Use tests and feedback loops to steer weak models toward correct patches.

- **Tight context control**  
  Avoid wasting limited model capacity on irrelevant repo state.

- **Explicit permissions**  
  Keep actions bounded, inspectable, and predictable.

- **Reproducible evaluation**  
  Measure outcomes under matched conditions instead of relying on anecdotes.

## What it is not

Villani Code is not trying to pretend a small local model is a frontier model.

It is not a general autonomous software engineer.
It is not a vague agent platform.
It is not built for flashy open-ended demos.

The goal is narrower:

**make small local models better at real repo tasks.**

## Where it should win

Villani Code is built for workflows where runtime quality matters enough to overcome weaker model capability:

- constrained bug fixes
- test-guided changes
- small refactors
- repo navigation and diagnosis
- privacy-sensitive local work
- cost-constrained coding workflows

The focus is not “best model.”
The focus is **best use of a small model.**

## Benchmark philosophy

Villani Code is built around a simple thesis:

**On small local models, a better runtime can beat more general-purpose coding-agent workflows.**

That should be proven under matched conditions:
- same model
- same task set
- same budget
- same repo constraints
- same evaluation rules

Key metrics:
- task success rate
- accepted diff rate
- token efficiency
- time to first useful diff
- unnecessary edit rate
- safety and permission behavior

## Design principles

### Built for weak models
Weak models need tighter prompts, narrower actions, better decomposition, and stronger validation.

### Terminal-first
Villani Code works in real repositories with files, tests, commands, and constraints.

### Safe by default
The agent should not get more trust than it earns.

### Measured by outcomes
The only thing that matters is whether the patch is useful, correct, and efficient.

## Status

Villani Code is early, opinionated, and focused.

It is for people who want:
- local-first coding workflows
- more value from small models
- explicit control over agent behavior
- serious evaluation on real tasks

## Thesis

**Small local models do not need hype. They need a runtime that wastes less of their capability.**

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
