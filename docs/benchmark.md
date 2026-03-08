# Villani Benchmark v1

Villani Benchmark v1 replaces the old JSON-pack benchmark and eval simulation with repository-grounded, deterministic, objectively scored tasks for terminal coding agents.

## Why the old benchmark was replaced

The previous benchmark/eval stack mixed subjective logic, legacy pack aliases, and simulation-oriented eval behavior. v1 removes that architecture and evaluates only bounded, self-contained repository tasks with explicit verification commands.

## Task families

- `bugfix`
- `repro_test`
- `localize_patch`
- `terminal_workflow`

## Task layout

Each task lives under `benchmark_tasks/villani_bench_v1/<task_id>/` with:

- `task.yaml` (typed schema)
- `prompt.txt` (single short instruction)
- `repo/` (seed repository copied into temp workspace)
- `visible_checks/` (optional visible assets)
- `hidden_checks/` (hidden verification assets; never copied to agent workspace)
- `metadata.json` (internal metadata)

## Scoring philosophy

Primary score is binary:

- `success = 1` only when visible + hidden verification both pass and policy constraints are met.
- otherwise `success = 0`.

Binary success rate is the headline metric.

## Hidden checks

Hidden checks are executed after the agent run from task-owned hidden data and are not exposed to the agent execution workspace.

For `repro_test` tasks, hidden grading runs the agent-written test against a hidden fixed reference copy.

## CLI

- `villani-code benchmark list --suite benchmark_tasks/villani_bench_v1`
- `villani-code benchmark run --suite benchmark_tasks/villani_bench_v1 --task bugfix_001_datetime_cli --agent villani --model <model> --base-url <url>`
- `villani-code benchmark run --suite benchmark_tasks/villani_bench_v1 --agent villani --model <model> --base-url <url>`
- `villani-code benchmark summary --results artifacts/benchmark/results.jsonl`

## Authoring new tasks

1. Create a new task folder under `benchmark_tasks/villani_bench_v1/`.
2. Add a bounded broken `repo/`.
3. Add `task.yaml` with required fields:
   - `id`, `family`, `difficulty`, `language`, `max_minutes`, `max_files_touched`,
     `expected_artifacts`, `visible_verification`, `hidden_verification`, `success_policy`, `allowlist_paths`.
4. Add a one-line `prompt.txt`.
5. Add `metadata.json` for internal diagnostics.
6. Keep checks deterministic, offline, and fast.

## Migration notes

Removed/replaced behavior:

- Removed old benchmark pack alias flow (`internal_regressions`, `general_coding`, `constrained_model`).
- Removed old benchmark command semantics that depended on legacy JSON task format.
- Replaced old benchmark reporting outputs with JSONL task-run records + summary JSON.
- Legacy eval simulation is no longer the benchmark path for agent comparison.

Use the new benchmark subcommands under `villani-code benchmark ...`.
