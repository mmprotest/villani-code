# Villani Benchmark v4 (runtime advantage focused)

Villani benchmark now explicitly measures **runtime advantage on the same backend model**, not just raw bugfix skill.

## Product thesis alignment

Primary question:

> Given the same constrained model, which agent runtime completes more real repo maintenance tasks with higher success, lower cost, and less intervention?

## Task design buckets

Each task now declares:

- `benchmark_bucket`: `baseline` or `runtime_stressing`
- `task_type`
- `runtime_stressors`

Use this split in analysis:

- **baseline**: obvious/small fixes (model competence floor)
- **runtime_stressing**: navigation, traps, iterative recovery, multi-stage repair (runtime design matters)

## Metadata schema (important new fields)

Task metadata includes:

- `name`, `difficulty`, `primary_skill`, `expected_files`, `reference_patch_size_lines`
- `runtime_stressors[]`
- `task_type`
- `requires_repo_navigation`
- `requires_multi_step_reasoning`
- `has_false_fix_trap`
- `requires_retry_recovery`
- `likely_tool_sequence[]`
- `evaluation_focus[]`
- `benchmark_bucket`

## New runtime-stressing tasks

Core suite now includes:

- `hidden_multi_file_bug`
- `false_fix_trap`
- `two_stage_fix`

These are deterministic, small-repo maintenance tasks with compact patches and explicit runtime stressors.

## Result metrics beyond pass/fail

Per-run outputs now include:

- Core outcomes: `success`, `pass_rate`, `failed`, `timed_out`
- Efficiency: `wall_clock_seconds`, `tokens_input/output/total`, `estimated_cost`, `model_name`, `agent_name`
- Interaction/runtime: `number_of_turns`, `tool_calls_total`, `file_reads`, `file_writes`, `patch_attempts`, `test_runs`, `retries_after_failure`, `first_pass_success`, `recovered_after_failed_attempt`
- Task fit: `expected_files_touched_count`, `actual_files_touched_count`, `touched_unexpected_files`

If an adapter cannot capture a metric, value is `null` and the run remains valid.

## Aggregation model

Aggregates are exported for:

- task
- task_type
- runtime_stressor
- agent
- model
- agent+model pair
- benchmark bucket

## Reporting outputs

Generated outputs include:

- `results.jsonl`
- `results.csv`
- `summary.json`
- `aggregates.json`
- `report.md`

`report.md` includes:

- overall leaderboard (agent/model/agent+model)
- same-model comparison table (prominent)
- runtime-stressor breakdown
- task-type breakdown
- efficiency summary
- task-by-task outcomes

## Recommended workflow

```bash
python -m villani_code.cli benchmark run --suite benchmark_tasks/villani_bench_v1 --agent villani --model <small-model>
python -m villani_code.cli benchmark summary --results artifacts/benchmark/results.jsonl
python -m villani_code.cli benchmark report --results artifacts/benchmark/results.jsonl --out artifacts/benchmark/report.md
```
