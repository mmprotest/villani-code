# Benchmark Tasks

Task suite lives in `benchmark_tasks/villani_bench_v1` and now includes 25+ deterministic offline tasks.

Each task directory contains:
- `task.yaml`
- `prompt.txt` (single short instruction)
- `repo/`
- optional `hidden_checks/` assets
- `metadata.json` (`source_type`, `tags`, `task_version`, etc.)

See `docs/benchmark.md` for scoring, anti-gaming policy, and CLI usage.
