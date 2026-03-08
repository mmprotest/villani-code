# Villani Benchmark v3 (hardened)

Villani benchmark is a layered evaluation **platform** for terminal coding agents, not a static task list.

## Headline scoring rule

`success = 1` only if visible checks, hidden checks, and policy checks all pass.

## Tracks (explicit metadata only)

- Every task **must** declare `benchmark_track` in task metadata (`core` or `feature`).
- Path-based inference was removed; no substring/path heuristics are used.
- Core and feature are reported separately and not merged into one headline score.

### Core vs feature intent

- **Core:** bounded bugfix/repro/localize/terminal tasks, tighter patch radius.
- **Feature:** larger end-to-end feature tasks with objective verification; still locally bounded.

## Adapter fairness + telemetry capability

Each adapter declares:

- `adapter_capability`
- `telemetry_capability`
- `fairness_classification`
- `fairness_notes`

Fairness classes:
- `exact_comparable`
- `approximately_comparable`
- `coarse_wrapper_only`
- `not_comparable`

Current caveats:
- `villani`: exact comparable when runtime events are emitted.
- `claude`, `opencode`, `copilot-cli`: coarse wrapper adapters; not process-level apples-to-apples.
- `cmd`/`shell`: not comparable (debug/smoke utility only).

## Telemetry honesty policy

Every telemetry-like field includes `telemetry_field_quality_map` (`exact|inferred|unavailable`).

- Exact: directly instrumented.
- Inferred: coarse calculation with caveat.
- Unavailable: null/None; never fabricated.

Important hardening changes:
- `num_shell_commands` and `num_failed_commands` are no longer faked from generic event length/exit code.
- Process metrics are only populated when quality justifies it.

## Health subsystem

Healthcheck now reports machine-readable `errors` and `warnings` and fails CI/CLI on serious issues.

Coverage includes:
- invalid tasks/schema
- invalid/missing track metadata
- duplicate task ids
- duplicate checksums
- missing visible/hidden checks
- broken allowlist
- leaked hidden assets in visible repo
- stale version warnings
- missing expected_files / primary_skill warnings

## Anti-gaming

Current enforced defenses:
- hidden verification required
- allowlist/forbidden-path policy
- benchmark asset integrity checks
- repro-test validity hardening (must fail broken + pass fixed)

Known limitation: task-by-task metamorphic variant coverage is still partial; framework is in place but not universal yet.

## Reporting and stats

Outputs:
- JSONL results
- JSON summary
- CSV export
- Markdown report
- HTML report

Summary/report now highlight:
- separate core vs feature summary
- fairness class slices
- telemetry quality slices
- hidden-fail-after-visible-pass rate
- invalid repro-test rate
- forbidden-edit rate
- solved-only runtime/diff medians
- small-sample warnings

## Migration notes (this hardening pass)

- fixed track inference bug: explicit metadata required; no path substring fallback.
- downgraded/removed misleading telemetry behavior.
- narrowed fairness claims for wrapper adapters.
- strengthened feature/core separation in docs/reporting/filters.
- expanded healthcheck integrity coverage and hard failure behavior.
- improved benchmark diagnostics and caveat surfacing.
