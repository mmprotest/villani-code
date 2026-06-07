# Changelog

## 0.1.0rc1

- Reduced runner telemetry regressions by separating compact model-visible command observations from full debug artifacts, bounding filesystem snapshots, handling command timeouts as tool results, fixing absolute Read path behaviour, and adding no-progress loop protection while preserving execution-context isolation.
- Hardened benchmark validation command resolution with explicit allowlisting and clear environment-failure classification.
- Added benchmark interpretation-status policy (`headline_comparable`, `informational_only`, `internal_only`) with blunt report banners.
- Expanded aggregate provenance reporting so environment/harness instability is separated from agent weakness in summaries.
- Added benchmark preflight checks for task packs, agent names, command structure, and likely executable-resolution issues.
- Curated benchmark starting packs with distinct task instructions and pack lint validation tests.
- Tightened CI release surface for Python 3.11 and Windows/core behavior, plus packaging smoke assertions.
- Updated benchmark documentation and added a practical release checklist.

- Added a general prompt-grounded final consistency check that tracks required deliverables, weakened validation evidence, unstable validation, unresolved self-identified defects, and last-known-better state before finalization.
