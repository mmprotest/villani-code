# Changelog

## 0.1.0rc1

- Improved private runtime root discovery for agent-issued command environment sanitization by deriving runner-private roots from runner-owned launch/config environment variables and executable paths, while keeping diagnostics artifact-only.
- Added generic sanitization for agent-issued command environments so target workspace commands do not inherit Villani private runtime paths, while preserving write-only diagnostics in debug artifacts.
- Hardened benchmark validation command resolution with explicit allowlisting and clear environment-failure classification.
- Added benchmark interpretation-status policy (`headline_comparable`, `informational_only`, `internal_only`) with blunt report banners.
- Expanded aggregate provenance reporting so environment/harness instability is separated from agent weakness in summaries.
- Added benchmark preflight checks for task packs, agent names, command structure, and likely executable-resolution issues.
- Curated benchmark starting packs with distinct task instructions and pack lint validation tests.
- Tightened CI release surface for Python 3.11 and Windows/core behavior, plus packaging smoke assertions.
- Updated benchmark documentation and added a practical release checklist.
