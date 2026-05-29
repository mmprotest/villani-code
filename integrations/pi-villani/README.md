# Pi Villani Integration

`pi-villani` adds Pi slash commands that delegate repository repair tasks to Villani Code. Pi remains the UI and model/auth host; Villani remains the runner that edits files and runs verification.

```text
/villani Fix the failing authentication tests and run the relevant verification
  -> Pi extension
  -> temporary 127.0.0.1 OpenAI-compatible proxy backed by Pi's active model
  -> villani-code bridge --stdio
  -> existing Villani Runner
  -> normalized progress/final events rendered in Pi
```

The extension does **not** port or duplicate Villani's runner logic in TypeScript.

## Requirements

- Node.js 22.19 or newer, matching the current Pi runtime packages.
- Villani Code installed in the Python environment visible to Pi. A standard install of this repository provides the `villani-code` executable.
- Pi with an active configured model, unless you opt into explicit Villani model configuration.

## Installation

Published package, once released:

```bash
pi install npm:pi-villani
```

Development/local checkout:

```bash
cd integrations/pi-villani
npm install
npm run build
pi install ./
```

For one-off testing without installing permanently, Pi supports loading a local package/extension for the current run:

```bash
pi -e ./integrations/pi-villani/dist/index.js
```

## Usage

```text
/villani Fix the failing authentication tests and run the relevant verification
/villani-abort
```

Only one Villani run is allowed per Pi extension instance at a time. If a run is active, a second `/villani` command is rejected with a message telling you to wait or run `/villani-abort`.

When Villani's existing permission policy classifies an operation as requiring approval, Pi asks before the operation executes. Approvals are per operation and are not remembered across runs.

## Defaults and configuration

Default behavior:

- `VILLANI_COMMAND` defaults to `villani-code`.
- `VILLANI_MODE` defaults to `runner`, which maps to normal `Runner.run(task)` execution.
- `VILLANI_MODE=villani` maps to the repository's autonomous `Runner.run_villani_mode()` path.
- Pi model reuse is enabled by default. The extension resolves Pi-managed credentials and provider headers with `ctx.modelRegistry.getApiKeyAndHeaders(model)`, starts a temporary local proxy, and passes that proxy URL to Villani as an OpenAI-compatible `base_url`.
- Upstream Pi API keys/OAuth/provider headers remain inside the Node extension/proxy and are passed only to Pi AI requests, never to the Villani subprocess.

Optional environment variables:

| Variable | Meaning |
| --- | --- |
| `VILLANI_COMMAND` | Override the Villani executable path/name. Defaults to `villani-code`. This is a single executable path, not a shell command. |
| `VILLANI_MODE` | `runner` or `villani`. Defaults to `runner`. |
| `VILLANI_USE_PI_MODEL` | Set to `false` to disable Pi-backed model reuse and use explicit Villani provider config. |
| `VILLANI_PROVIDER` | Explicit fallback provider (`openai` or `anthropic`) when `VILLANI_USE_PI_MODEL=false`. |
| `VILLANI_MODEL` | Explicit fallback model. |
| `VILLANI_BASE_URL` | Explicit fallback model endpoint. |
| `VILLANI_API_KEY` | Explicit fallback API key, if needed. |

macOS/Linux example for explicit fallback:

```bash
export VILLANI_USE_PI_MODEL=false
export VILLANI_COMMAND=villani-code
export VILLANI_PROVIDER=openai
export VILLANI_MODEL=your-model
export VILLANI_BASE_URL=http://127.0.0.1:1234
export VILLANI_API_KEY=dummy
```

Windows PowerShell example:

```powershell
$env:VILLANI_USE_PI_MODEL = "false"
$env:VILLANI_COMMAND = "C:\Program Files\Python\Scripts\villani-code.exe"
$env:VILLANI_PROVIDER = "openai"
$env:VILLANI_MODEL = "your-model"
$env:VILLANI_BASE_URL = "http://127.0.0.1:1234"
$env:VILLANI_API_KEY = "dummy"
```

## Final output

Pi renders operational progress only: start, phases, tool/file activity, verification, governor redirects, failure and abort status. It does not render Villani prompts, hidden reasoning, raw JSON or full transcripts.

Final output separates attribution:

```text
Villani completed

Summary:
Fixed failing auth tests.

Changed by Villani:
- src/auth.py

Pre-existing workspace changes excluded from attribution:
- notes.txt

Verification passed: true
Transcript: .villani_code/runs/...
```

## Safety and approvals

- Villani operates in the current repository and may edit files and run verification commands through its normal permission/sandbox path.
- The Pi bridge honours Villani's existing permission classifier: `ALLOW` operations run automatically, `ASK` operations prompt through Pi before execution, and `DENY` operations do not execute.
- The default Pi integration does not silently approve file writes, patches, or shell commands that Villani classifies as requiring approval.
- Approval prompts show concise operation context: write/patch requests show the affected path, and Bash requests show the command being requested. Large file contents and patches are not dumped into the prompt.
- Rejecting an approval returns a normal denied-tool result to Villani; Villani may continue or fail according to its existing runner behavior.
- `/villani-abort` cancels a run even while an approval decision is pending; pending approvals are resolved as denied.
- Pre-existing dirty files are reported separately from files changed by Villani.
- The Pi model proxy binds only to `127.0.0.1`, uses a random available port, and exists only for the active `/villani` run.
- Pi credentials stay in Pi; the Python child receives only a localhost proxy URL in the default path. Model credentials are unrelated to tool approvals and are never shown in approval prompts.
- Pi packages execute with normal user permissions. Review source before installing third-party packages.

## Limitations

- `/villani-abort` cancels startup, pending approval prompts, and in-flight Pi model requests immediately via a per-run `AbortController`, sends a bridge abort request if the bridge exists, and force-kills the subprocess after a short timeout if the Python runner does not stop. Already-running Python child commands may not be interruptible until the current Villani operation returns.
- Non-interactive Pi contexts without a usable confirmation UI deny approval-required operations by default rather than auto-approving them.
- The Pi proxy implements Villani's current OpenAI-compatible `/v1/chat/completions` path. It does not implement `/v1/messages` because the bridge points Villani at the proxy with `provider=openai`.
- Streaming is compatibility streaming: the proxy calls Pi via `complete()` and emits the final response as one OpenAI SSE chunk plus `[DONE]`, not token-by-token streaming.
- Non-git repositories use lower-confidence changed-file reporting based on Villani mutation events only.

## Troubleshooting

- **`villani-code` not found:** install Villani Code in the environment Pi uses, or set `VILLANI_COMMAND` to the full executable path.
- **Python environment mismatch:** run `villani-code bridge --stdio` in the same shell environment used to start Pi.
- **No Pi model available:** select/configure a Pi model, or set `VILLANI_USE_PI_MODEL=false` and provide explicit Villani provider variables.
- **Pi auth resolution fails:** confirm the selected Pi model is logged in/configured; auth errors are sanitized before display.
- **Bridge readiness timeout:** run `printf '{"type":"ping","id":"manual-test"}\n' | villani-code bridge --stdio` and confirm it prints `ready` then `pong`.
- **Proxy startup failure:** ensure no local security tool blocks binding to `127.0.0.1` on random ports.
- **Proxy upstream error:** the proxy returns a sanitized HTTP error to Villani instead of a fake empty completion; check Pi model/auth configuration.
- **Approval prompt unavailable:** in headless/non-interactive contexts, approval-required operations are denied for safety. Run Pi interactively or provide an explicit test approval handler.
- **Smoke test:** from this checkout, run `cd integrations/pi-villani && npm install && npm test`, then `printf '{"type":"ping","id":"manual-test"}\n' | villani-code bridge --stdio`.
