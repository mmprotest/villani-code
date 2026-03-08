# Villani Benchmark v2 (hardened)

Villani benchmark is now an evaluation **system** for terminal coding agents, not just a task list.

## What it measures

- Final repository correctness on bounded, deterministic tasks.
- Headline metric remains binary task success (`visible_pass && hidden_pass && policy_pass`).
- Hidden checks and policy constraints enforce anti-gaming.

## What it does not measure

- Subjective code quality as primary score.
- Agent self-reported confidence.
- Internet-dependent workflows.

## Architecture

- `task_loader.py`: typed task loading, filtering, checksums.
- `adapters/`: Villani/Claude/OpenCode/Copilot/generic command adapters.
- `workspace.py`: isolated per-run workspace lifecycle with guaranteed cleanup unless `--keep-workspace`.
- `verifier.py`: visible/hidden subprocess verification with timestamps.
- `runner.py`: policy enforcement, reproducibility manifests, repro-test grading.
- `reporting.py`: machine + human summaries, diagnostics, CSV + markdown reports.
- `stats.py`: confidence intervals and paired bootstrap delta.

## Fairness and comparability

Exact comparability: same task set, same timeout, same model backend/provider settings, same deterministic options.
Approximate comparability: different agent CLIs with different tool capabilities or telemetry support.
Unsupported: cross-agent claims that rely on unavailable telemetry precision.

## Telemetry quality levels

- `exact`: adapter emits structured events.
- `inferred`: derived from coarse execution metadata.
- `unavailable`: only raw stdout/stderr available.

Inferred fields are explicitly tracked in result rows.

## Task families

- `bugfix`
- `repro_test`
- `localize_patch`
- `terminal_workflow`

Task metadata supports: source type (`seeded|curated|mutated`), tags, task version, checksum.

## CLI

- `villani-code benchmark list --suite ... [--family --difficulty --tag --source-type]`
- `villani-code benchmark run --suite ... [--task ...] --agent ... [--repeat N] [--keep-workspace]`
- `villani-code benchmark summary --results artifacts/benchmark/results.jsonl`
- `villani-code benchmark stats --results artifacts/benchmark/results.jsonl`
- `villani-code benchmark compare --results-a ... --results-b ...`
- `villani-code benchmark report --results ... --out artifacts/benchmark/report.md`

## Repro-test grading rules

Candidate test must:
1. fail on broken repo,
2. pass on hidden fixed repo,
3. fail meaningfully (not syntax/import noise).

## Statistical honesty

- Success rates include Wilson 95% confidence interval.
- Paired comparison reports bootstrap CI for pass-rate delta.
- Reports avoid strong claims on tiny shared sample sizes.

## Migration notes

From v1 to v2:
- Added real adapter abstraction + structured run model.
- Added reproducibility manifests and task checksums.
- Added robust workspace cleanup semantics + debug preservation flag.
- Added normalized failure reasons and telemetry quality fields.
- Added diagnostics/statistics reporting and paired comparison.
- Expanded task suite to >=25 tasks with tags/source metadata.
