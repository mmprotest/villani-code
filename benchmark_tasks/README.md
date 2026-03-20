# Benchmark Tasks

## Suites

- Core suite: `benchmark_tasks/villani_bench_v1`
- Feature suite: `benchmark_tasks/villani_feature_v1`
- Long-form suite: `benchmark_tasks/villani_long_bench_v1`
- Mini suite: `benchmark_tasks/villani_mini_bench_v1`

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
- `benchmark_category`
- `task_type`
- `requires_repo_navigation`
- `requires_multi_step_reasoning`
- `has_false_fix_trap`
- `requires_retry_recovery`
- `likely_tool_sequence`
- `evaluation_focus`
- `benchmark_bucket` (`baseline` or `runtime_stressing`)

## Canonical taxonomy

- `benchmark_category` is the canonical top-level work taxonomy. Allowed values are:
  `bug_fix`, `failing_test_diagnosis`, `refactor`, `config_tooling_repair`, and `small_feature_work`.
- `task_type` is still important, but it describes execution shape or structural constraints such as
  `single_file_bugfix`, `repo_navigation_bugfix`, or inspect/bounded workflows.
- Directory prefixes are legacy naming hints only. A task under `localize_*` or `terminal_*` still uses the
  canonical `benchmark_category` value stored in both `task.yaml` and `metadata.json`.

## Runtime-stressing additions

Core suite includes dedicated runtime stress tasks:

- `hidden_multi_file_bug`
- `false_fix_trap`
- `two_stage_fix`

Each includes deterministic failing tests and a compact `reference_patch.diff`.

See `docs/benchmark.md` for reporting and interpretation guidance.
