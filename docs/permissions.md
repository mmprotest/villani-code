# Permissions

Rule order is strict: deny, ask, allow. First match wins.

Default deny examples:
- `Read(.env)`
- `Read(secrets/**)`
- `Bash(curl *)`
- `Bash(wget *)`

Bash matching is operator-aware and token-based.


Bash policy: ASK by default. If `BashSafe(*)` is present in allow rules, a tokenized safe-command allowlist can auto-approve common read-only and test commands (e.g. `pwd`, `git status`, `pytest`). Commands with chaining (`&&`, `;`, `|`), redirection (`>`, `2>`), or subshell expansion remain ASK. Install commands also remain ASK.
