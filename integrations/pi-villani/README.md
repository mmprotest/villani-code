# Pi Villani Integration

This package is a thin Pi extension that lets Pi remain the chat UI/shell while Villani Code remains the autonomous coding runner.

Pi command flow:

```text
/villani Fix the failing tests and verify the repair
  -> Pi extension
  -> villani bridge --stdio subprocess in the current workspace
  -> JSONL run command
  -> existing Villani Runner
  -> normalized JSONL progress/final events back to Pi
```

The extension does **not** reimplement Villani's runner logic and does not require Pi to understand Villani internals.

## Local installation from this checkout

```bash
cd integrations/pi-villani
npm install
npm run build
```

Then load/register the built extension with your Pi extension development workflow. The exact Pi SDK packaging API is still isolated behind `src/index.ts`; if your installed Pi SDK exposes a different command registration method, adapt only that file.

## Required Villani installation

The extension launches:

```bash
villani bridge --stdio
```

Set `VILLANI_COMMAND` if your executable name is different, for example this repository currently exposes the console script as `villani-code` and module execution works as `python -m villani_code.cli`:

```bash
export VILLANI_COMMAND="villani-code"
# or
export VILLANI_COMMAND="python -m villani_code.cli"
```

## Configuration

The first milestone uses explicit Villani model configuration. Pi-backed model reuse is not implemented yet.

Supported environment variables:

```bash
export VILLANI_COMMAND="villani"
export VILLANI_MODE="runner"        # runner or villani
export VILLANI_PROVIDER="openai"    # openai or anthropic
export VILLANI_MODEL="your-model"
export VILLANI_BASE_URL="http://127.0.0.1:1234"
export VILLANI_API_KEY="dummy"
```

`VILLANI_PROVIDER`, `VILLANI_MODEL`, `VILLANI_BASE_URL`, and `VILLANI_API_KEY` are sent to the bridge only when set. Villani's own defaults and validation remain the source of truth.

## Usage

Inside Pi, from a workspace/repository:

```text
/villani Fix the failing auth tests and verify the repair
```

The final Pi summary includes:

- success, failure, or aborted status
- Villani's summary
- changed files reported from the repository working tree
- verification status when Villani exposes it
- transcript/run path when Villani writes one

## Safety notes

- The extension does not bypass Villani permissions or sandboxing.
- The extension launches a subprocess with `shell: false` for Windows-friendly argument handling.
- The bridge emits operational telemetry only; it does not stream hidden reasoning, model prompts, or transcript contents.

## Limitations

- Cancellation is best-effort. The bridge sends `abort` and marks the run as aborted only after the current Villani runner call stops; the core runner does not yet expose a universal cooperative cancellation token.
- Pi SDK typings are not vendored in this repository. The extension uses a small `PiLikeContext` shim so the bridge/process/rendering code stays compile-safe and the real Pi API adapter can remain small.
- Pi model/auth reuse is deferred. See `docs/pi-model-bridge.md` for the intended design.

## Troubleshooting

- **No ready event / timeout:** confirm `VILLANI_COMMAND` is on `PATH` and supports `bridge --stdio`.
- **Missing model/base URL:** set `VILLANI_MODEL` and `VILLANI_BASE_URL`, or configure Villani defaults if supported by your installation.
- **Unexpected text on stdout:** stdout must be JSONL protocol only. Send debug logs to stderr.
- **Windows paths:** pass normal workspace paths; the bridge normalizes changed-file paths to forward slashes in events.
