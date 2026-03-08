# Benchmark Tasks

## Suites

- Core suite: `benchmark_tasks/villani_bench_v1`
- Feature suite: `benchmark_tasks/villani_feature_v1`

## Task directory contract

Each task directory contains:
- `task.yaml` (must include `benchmark_track: core|feature`)
- `prompt.txt` (single short instruction)
- `repo/`
- optional `hidden_checks/` assets
- `metadata.json` (source/version/tags/expected_files/skills)

## Hardening notes

- Track inference from path names is removed.
- Core tasks may contain words like `feature_flag` in ids/paths without becoming feature-track.
- Hidden checks should include anti-overfit variants where practical.
- Keep `expected_files` + `primary_skill` populated for process diagnostics and health quality.

See `docs/benchmark.md` for telemetry quality, fairness caveats, healthcheck scope, and reporting semantics.
