# Refactor TODO (high-risk modules)

This repository still has high-complexity modules where safe decomposition needs dedicated work.

## `villani_code/autonomous.py`

Current issue: `VillaniModeController` owns planning, execution, retry policy, stop-reason policy, event emission, and reporting state. This is difficult to reason about and easy to regress.

Recommended split:

1. `autonomous_loop.py`
   - wave orchestration
   - budget checks
   - stop-decision transitions
2. `autonomous_attempts.py`
   - task attempt lifecycle
   - retry and lineage bookkeeping
3. `autonomous_reporting.py` (already exists; expand use)
   - summary generation
   - event payload shaping

Risk notes:
- Many tests monkeypatch internals; refactor should preserve public method/field names during transition.
- Stop-reason behavior is contract-like and should be protected by snapshot tests.

## `villani_code/state.py`

Current issue: `Runner` combines request shaping, tool execution policy, context handling, autonomy routing, and output formatting.

Recommended split:

1. `runner_request.py`
   - prompt and request payload assembly
2. `runner_execution.py`
   - tool invocation loop and policy checks
3. `runner_modes.py`
   - mode routing (`run`, interactive support, villani mode entry)

Risk notes:
- This file is a central integration point; move incrementally and keep behavior tests green after each extraction.
- Preserve CLI-facing behavior and response object shape.
