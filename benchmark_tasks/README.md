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
- `metadata.json` with taxonomy and stress metadata

## Metadata requirements

Every task metadata file should include:

- `name`
- `difficulty`
- `primary_skill`
- `expected_files`
- `reference_patch_size_lines`
- `runtime_stressors`
- `task_type`
- `requires_repo_navigation`
- `requires_multi_step_reasoning`
- `has_false_fix_trap`
- `requires_retry_recovery`
- `likely_tool_sequence`
- `evaluation_focus`
- `benchmark_bucket` (`baseline` or `runtime_stressing`)

## Runtime-stressing additions

Core suite includes dedicated runtime stress tasks:

- `hidden_multi_file_bug`
- `false_fix_trap`
- `two_stage_fix`

Each includes deterministic failing tests and a compact `reference_patch.diff`.

See `docs/benchmark.md` for reporting and interpretation guidance.
