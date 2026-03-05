# Permissions

Rule order is strict: deny, ask, allow. First match wins.

Default deny examples:
- `Read(.env)`
- `Read(secrets/**)`
- `Bash(curl *)`
- `Bash(wget *)`

Bash matching is operator-aware and token-based.


Bash policy: ASK by default. If `BashSafe(*)` is present in allow rules, a tokenized safe-command allowlist can auto-approve common read-only and test commands (e.g. `pwd`, `git status`, `pytest`). Commands with chaining (`&&`, `;`, `|`), redirection (`>`, `2>`), or subshell expansion remain ASK. Install commands also remain ASK.


## Villani mode approval behavior

In Villani mode, normal ASK-level approvals (for edits and routine repo commands) are auto-resolved so autonomy is non-interactive. Hard DENY rules and tool-level safety protections (including shell denylist checks) are still enforced unless `--unsafe` is explicitly set.
