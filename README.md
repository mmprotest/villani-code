# Villani Code

**The coding agent runtime built to make smaller local models actually useful.**

Most coding agents are built around strong hosted models and large context budgets.

Villani Code is built for the opposite case: constrained local models, real repositories, explicit control, and work that has to survive contact with tests.

On a matched Qwen3.5 9B benchmark, excluding the broken repro track, Villani Code reached **75.0%** task success versus **32.1%** for Claude Code.

![Qwen3.5 9B benchmark comparison](villani_vs_claude_qwen35_9b.png)

## What Villani Code is

Villani Code is a terminal-first coding agent runtime designed for private, local-first, cost-sensitive development workflows.

It is built for the cases where model capability is limited and runtime quality matters more:
- bounded bug fixes
- repo navigation and localization
- test-guided iteration
- constrained maintenance work
- private codebases
- local inference setups

This is not a wrapper trying to make a weak model look impressive in a demo.

It is a runtime built to get more accepted work out of smaller models on real code.

## The idea

Small local models are cheap, private, fast to run, and easy to deploy.

They are also easy to waste.

A weak model with a sloppy runtime drifts, edits the wrong files, burns context, overexplains, and breaks the repo.

A weak model with tighter execution can still be useful.

That is the bet behind Villani Code.

## Why it exists

Most people evaluating coding agents ask the wrong question.

They ask: *what is the strongest model?*

That matters, but it misses the real constraint in a lot of environments:
- private repositories
- on-prem deployment
- limited GPU budgets
- local developer workflows
- enterprise teams that cannot send code to frontier APIs by default

In those settings, the question changes:

**How much useful repo work can you get from a smaller local model?**

That is the problem Villani Code is built to solve.

## What makes it different

### Built for constrained models
Villani Code is designed around the weaknesses of smaller models instead of pretending they do not exist.

### Terminal-first
It works where coding agents actually have to work: files, commands, tests, diffs, and repo state.

### Tighter task discipline
The runtime is built to reduce drift, keep actions bounded, and push the model toward useful edits instead of vague activity.

### Outcome-focused
The goal is not a pretty transcript. The goal is a patch that survives verification.

### Local-first by design
Villani Code is a better fit for privacy-sensitive and budget-sensitive environments than tools that only really shine with expensive hosted models.

## What the benchmark result actually means

The strongest current result is not “Villani beats everything everywhere.”

The stronger and more honest claim is narrower:

**Villani Code can get materially better coding performance out of smaller local models than general-purpose coding-agent workflows on the kinds of bounded repo tasks that matter in practice.**

That is the interesting thing.

Not that it looks smart.
Not that it writes long explanations.
Not that it can talk about code.

That it lands more correct work.

## Where it should win

Villani Code is a good fit when you care about one or more of these:
- private code must stay inside your environment
- model budget matters
- you want better performance from 7B to 35B-class local backends
- your work is bounded and verifier-friendly
- you care about explicit control over what the agent touches
- you want a runtime that is measured by useful diffs, not vibes

## Where it is not trying to win

Villani Code is not trying to be:
- a general autonomous software engineer
- a flashy open-ended demo bot
- a frontier-model replacement
- a generic AI chat shell with code bolted on

That is not the point.

The point is to make smaller local models much harder to dismiss.

## Installation

```bash
pip install .[tui]
```

Headless CLI only:

```bash
pip install .
```

Development dependencies:

```bash
pip install .[dev]
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

## Benchmarking

Run the benchmark suite:

```bash
villani-code benchmark run --suite benchmark_tasks/villani_bench_v1 --agent villani --provider openai --model your-model --base-url http://127.0.0.1:1234 --api-key dummy --output-dir artifacts/benchmark/run_name
```

Generate summary stats:

```bash
villani-code benchmark summary --results artifacts/benchmark/run_name/results.jsonl
villani-code benchmark stats --results artifacts/benchmark/run_name/results.jsonl
```

## Current thesis

A better runtime can move the needle more than people think.

Especially when the backend is small, local, private, and easy for everyone else to underestimate.

That is what Villani Code is trying to prove.
