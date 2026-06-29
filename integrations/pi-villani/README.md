# @mmprotest/pi-villani
Install with:
```bash
pi install npm:@mmprotest/pi-villani
```
Provides `/villani <task>`.

Runtime version: `v0.1.5`.

## Usage

Run Villani with `/villani <task>`. Active runs are still cleaned up automatically on process/session cancellation or error cleanup.

# Villani Code

**Flagship coding-agent performance from small local models.**

Villani Code is a local-first coding-agent runtime designed to make smaller open models do real repository work: navigate files, run commands, make patches, survive verification, and keep working through messy terminal environments.

The thesis is simple: small models do not just need better weights. They need a better runtime.

## Terminal-Bench 2.0: Qwen3.6 27B full-suite run

Villani Code achieved a **196/445 lower-bound score** on the full Terminal-Bench 2.0 suite using **Qwen3.6 27B**.

That is **44.0%** across **89 tasks** with **5 attempts per task**.

### Headline result

| System | Model | Terminal-Bench 2.0 accuracy |
|---|---|---:|
| Codex CLI | GPT-5-Codex | 44.3% |
| **Villani Code** | **Qwen3.6 27B** | **44.0%** |
| Mini-SWE-Agent | GPT-5-Codex | 41.3% |
| Claude Code | Claude Sonnet 4.5 | 40.1% |
| Dakou Agent | Qwen 3 Coder 480B | 27.2% |
| little-coder | Qwen3.6-35B-A3B | 24.6% |
| Bash Agent | TermiGen-32B | 19.3% |
| little-coder | Qwen3.5-9B | 9.2% |

## Qwen3.5 9B same-model runtime comparison

Villani Code was also tested against Claude Code using the same model: **Qwen3.5 9B**.

Same model. Same tasks. Different agent runtime.

Villani Code won.


| Runner | Score | Success rate |
|---|---:|---:|
| **Villani Code + Qwen3.5 9B** | **38/60** | **63.3%** |
| Claude Code + Qwen3.5 9B | 26/60 | 43.3% |

**Villani Code delivered a 46% relative performance improvement over Claude Code.**

This comparison covers **12 overlapping Terminal-Bench tasks**, with **5 runs per task**, for **60 runs per agent**.

Villani Code won **6 tasks**, tied **6 tasks**, and lost **0**.

## What Villani Code is

Villani Code is a terminal-first coding agent for:

- bounded bug fixes
- repo navigation and localization
- command-driven debugging
- test-guided patching
- local inference setups
- privacy-sensitive codebases
- smaller open model backends

It is built for the environment where most coding agents start to fall apart: smaller models, hard verification, constrained context, terminal noise, failed commands, and real repositories.

## What changed in the upgraded runtime

The latest Villani Code upgrade includes:

- new execution loop
- better local model integration
- cleaner tool handling
- improved failure recovery
- task-scoped memory system
- better state tracking across long-running coding tasks

The benchmark comparison evaluates the upgraded runtime as a whole.