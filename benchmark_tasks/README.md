# Benchmark Tasks

## Suites

- Core suite: `benchmark_tasks/villani_bench_v1` (25+ deterministic offline tasks).
- Feature suite scaffold: `benchmark_tasks/villani_feature_v1` (initial feature-scale tasks).
- Optional held-out/private suites can be provided via CLI `--private-suite`.

## Task directory contract

Each task directory contains:
- `task.yaml`
- `prompt.txt` (single short instruction)
- `repo/`
- optional `hidden_checks/` assets
- `metadata.json` (source type, task version, tags, variant metadata)

## Authoring notes

- Keep prompts short, bounded, objective.
- Verification must remain execution-based and deterministic.
- Hidden checks should include anti-overfit variants where possible.
- Populate provenance (`source_type`) and version fields.

See `docs/benchmark.md` for scoring, policy, telemetry quality semantics, reporting, and migration details.
