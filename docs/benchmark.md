# Villani Benchmark v3 (evaluation platform)

Villani benchmark is a layered evaluation **platform** for terminal coding agents, not a static task list.

## Headline scoring rule (non-negotiable)

`success = 1` **only if** all are true:
- visible verification passes
- hidden verification passes
- policy checks pass

Else `success = 0`.

Binary success rate is the headline metric. All other metrics are diagnostics.

## Tracks

- **Core track** (`benchmark_track=core`): bounded bugfix/repro/localize/terminal tasks, small patch radius.
- **Feature track** (`benchmark_track=feature`): feature-scale tasks, separate reporting, not mixed into core score.

## Platform layers

1. public core suite
2. private/held-out suite support
3. rolling refresh via task version + checksum + variant fields
4. task execution harness
5. verification subsystem (visible + hidden)
6. anti-gaming policy subsystem
7. telemetry subsystem (exact/inferred/unavailable)
8. reporting subsystem (jsonl/json/csv/markdown/html)
9. statistical subsystem (Wilson CI + paired bootstrap)
10. benchmark health subsystem

## Typed task model

Task schema supports:
- identity/versioning: `id`, `task_version`, `task_checksum`, `source_type`
- benchmark routing: `benchmark_track`, `family`, `difficulty`, `language`, `tags`
- constraints: `max_minutes`, `max_files_touched`, `allowlist_paths`, `forbidden_paths`
- verification: `visible_verification`, `hidden_verification`, `success_policy`
- variant/mutation: `task_variant_family`, `variant_id`
- metadata: expected files, skills, failure mode notes

## Adapter system and fairness

Adapters:
- Villani adapter
- Claude Code adapter
- OpenCode adapter
- Copilot CLI adapter
- generic shell command adapter (`cmd:` / `shell:`)

Each adapter reports fairness classification:
- `exact_comparable`
- `approximately_comparable`
- `coarse_wrapper_only`
- `not_comparable`

And explicit telemetry fidelity for fields:
- `exact`
- `inferred`
- `unavailable`

## Telemetry honesty

Results include:
- task metadata (track/family/difficulty/language/source/tags/version/checksum)
- adapter metadata (name/version/fairness)
- strict success/visible/hidden/failure reason
- runtime + patch stats + verification attempts
- telemetry quality + per-field quality map
- reproducibility manifest path
- repeat index for stability runs

No guessed fields are presented as exact.

## Reproducibility and hardening

- fresh workspace per run
- cleanup by default, `--keep-workspace` for debugging
- baseline git init + diff stats
- reproducibility manifest records benchmark/task/adapter/runtime checksums and environment details

## Anti-gaming policy

- allowlist path enforcement
- forbidden path enforcement
- benchmark asset integrity checks
- invalid artifact / no-op edits rejected by policy
- hardened repro-test grading (must fail on broken + pass on fixed, meaningful failure)

## Statistics and reporting

- Wilson CI for pass rate
- paired bootstrap delta CI
- stability summaries for repeated runs
- diagnostics by track/family/difficulty/language/source/telemetry/fairness

Outputs:
- JSONL results
- JSON summary
- CSV export
- Markdown report
- HTML report

## CLI

- `villani-code benchmark list --suite ... [--private-suite ... --include-private] [--track core|feature]`
- `villani-code benchmark run --suite ... --agent ... [--track ... --repeat N --keep-workspace]`
- `villani-code benchmark summary --results ...`
- `villani-code benchmark stats --results ...`
- `villani-code benchmark compare --results-a ... --results-b ...`
- `villani-code benchmark report --results ... --format markdown|html --out ...`
- `villani-code benchmark healthcheck --suite ...`
- `villani-code benchmark validate-tasks --suite ...`
- `villani-code benchmark manifest --results ...`

## Migration notes (v2 -> v3)

- expanded task/result schema with track/source/version/variant/fairness fields
- added held-out/private suite loading support
- added feature-track scaffolding (`benchmark_tasks/villani_feature_v1`)
- added anti-gaming policy module and benchmark healthchecks
- added reproducibility manifest checksums for repo + verifier command sets
- added telemetry field quality map (`exact|inferred|unavailable`)
- added repeat-indexed stability summaries and richer reporting slices
