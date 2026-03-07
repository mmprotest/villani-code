# Repository Code Review (2026-03-04)

## Scope
- Reviewed architecture, security boundaries, and reliability across `villani_code/` and tests.
- Ran full automated test suite to confirm current baseline behavior.

## What looks good
- Strong baseline test coverage (`62` tests passing) across tools, permissions, streaming, UI, MCP, and checkpoints.
- Permission model is explicit (`deny -> ask -> allow`) and integrates with event logging.
- Tool input schemas use Pydantic with `extra="forbid"`, reducing accidental over-posting.

## Findings

### 1) High: Path containment check can be bypassed for sibling directories
**Where:** `villani_code/tools.py::_safe_path`

`_safe_path` verifies containment with a string prefix comparison (`str(path).startswith(str(repo_resolved))`). This is not a safe filesystem boundary check because a sibling path like `/workspace/villani-code-malicious/file` still starts with `/workspace/villani-code` as a string prefix.

**Impact:** File tools (`Read`, `Write`, `Patch`, `Bash cwd`) can incorrectly accept paths outside the repository root when a crafted relative path resolves to a sibling with a matching prefix.

**Recommendation:** Replace prefix matching with `Path.is_relative_to(...)` (Python 3.11+) or a robust `try: path.relative_to(repo_resolved)` check.

---

### 2) Medium: Bash rule parser can raise and crash permission evaluation
**Where:** `villani_code/permissions.py::bash_matches`

`bash_matches` calls `shlex.split(command)` and `shlex.split(pattern)` without handling parse errors. Malformed shell input (e.g., unmatched quotes) raises `ValueError`, which propagates through policy evaluation.

**Impact:** A malformed command string may terminate the loop instead of returning a safe `ASK`/`DENY` decision.

**Recommendation:** Wrap tokenization in `try/except ValueError` and default to `False` (for matcher) or `ASK` (for classification path).

---

### 3) Medium: Checkpoints are skipped for ASK-approved edit tools
**Where:** `villani_code/state.py` tool execution branch

For `Write`/`Patch`, checkpoint creation currently happens only in the direct-allow branch. If policy returns `ASK` and the user approves, execution proceeds without creating a checkpoint first.

**Impact:** Inconsistent rewind/safety behavior based on policy route, potentially losing rollback points for user-approved edits.

**Recommendation:** Centralize pre-edit checkpoint creation so it runs before every actual `Write`/`Patch` execution (both ALLOW and ASK+approved paths).

---

### 4) Low: Mutable defaults in Pydantic models
**Where:** `villani_code/tools.py` (`LsInput.ignore`, `GitSimpleInput.args`)

List defaults are declared as literals (`[]` / `[... ]`) instead of using `Field(default_factory=...)`.

**Impact:** Pydantic often copies defaults safely, but mutable literals are still a foot-gun and reduce clarity; future refactors may accidentally rely on shared state assumptions.

**Recommendation:** Use `Field(default_factory=...)` for list defaults.

## Suggested next steps
1. Patch `_safe_path` and add targeted regression tests for sibling-prefix escape attempts.
2. Harden `bash_matches` with parse-error handling and add tests for malformed quotes.
3. Ensure checkpoint creation is policy-path agnostic for edit tools.
4. Clean up mutable defaults in models and add linting rule(s) to prevent reintroduction.
