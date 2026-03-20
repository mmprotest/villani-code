# Runner core contract

## Runner core
The runner core is limited to these modules:
- `villani_code/state.py`
- `villani_code/state_runtime.py`
- `villani_code/state_tooling.py`

## Layered consumers
These entrypoints and surfaces consume the core instead of extending it:
- `villani_code/autonomous.py`
- `villani_code/cli.py`
- `villani_code/interactive.py`
- `villani_code/tui/**`
- `villani_code/benchmark/**`

## Guardrails
- Do not add new benchmark-only logic inside the core modules.
- `Runner` is orchestration only.
- Tool policy belongs in `state_tooling.py`.
- Runtime/session/message shaping belongs in `state_runtime.py`.
- Autonomous mode must consume the runner and must not duplicate the main execution loop.
