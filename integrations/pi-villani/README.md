# Villani for Pi

Villani is a high-reliability coding runner for Pi. Pi stays the UI and model/auth host; Villani performs repository repair, edits, verification, and approval-bound operations through its existing runner.

Install:

```bash
pi install npm:@mmprotest/pi-villani
```

Use inside any repository:

```text
/villani Fix the failing tests and verify the result
/villani-abort
```

On first use, the extension automatically downloads a platform-specific standalone Villani runtime from GitHub Releases, verifies its SHA-256 checksum, extracts it into a private user cache, and launches the bundled `bridge --stdio` runtime. No separate Python, pip, virtual environment, or `villani-code` installation is required for normal use.

Villani uses your active Pi model through the local Pi-backed proxy and asks for approval before protected file or shell operations.

## Supported platforms

Runtime downloads are prepared for:

- Windows x64 (`win32-x64`)
- macOS Apple Silicon (`darwin-arm64`)
- macOS Intel (`darwin-x64`)
- Linux x64 (`linux-x64`)

Other platforms can still use a local Villani installation through `VILLANI_COMMAND`.

## Runtime cache

The runtime cache is outside the repository being edited:

- Windows: `%LOCALAPPDATA%\pi-villani\runtime\<version>\<platform-arch>\`
- macOS/Linux: `~/.cache/pi-villani/runtime/<version>/<platform-arch>/`

The extension verifies `checksums.txt` from the matching GitHub Release before extraction and writes a `.verified.json` marker before reusing a cached runtime.

## Developer override

For development or troubleshooting, set `VILLANI_COMMAND` to a single executable path/name. This skips runtime download and preserves Windows paths with spaces.

PowerShell example:

```powershell
$env:VILLANI_COMMAND = "C:\path\to\villani-code\.venv\Scripts\villani-code.exe"
pi
```

## Defaults and configuration

Default behavior:

- `VILLANI_COMMAND` unset means the extension downloads/reuses the verified standalone Villani runtime. Set it only for development/troubleshooting overrides.
- `VILLANI_MODE` defaults to `runner`, which maps to normal `Runner.run(task)` execution.
- `VILLANI_MODE=villani` maps to the repository's autonomous `Runner.run_villani_mode()` path.
- Pi model reuse is enabled by default. The extension resolves Pi-managed credentials and provider headers with `ctx.modelRegistry.getApiKeyAndHeaders(model)`, starts a temporary local proxy, and passes that proxy URL to Villani as an OpenAI-compatible `base_url`.
- Upstream Pi API keys/OAuth/provider headers remain inside the Node extension/proxy and are passed only to Pi AI requests, never to the Villani subprocess.

Optional environment variables:

| Variable | Meaning |
| --- | --- |
| `VILLANI_COMMAND` | Developer/troubleshooting override for the Villani executable path/name. When set, runtime download is skipped. This is a single executable path, not a shell command. |
| `VILLANI_MODE` | `runner` or `villani`. Defaults to `runner`. |
| `VILLANI_USE_PI_MODEL` | Set to `false` to disable Pi-backed model reuse and use explicit Villani provider config. |
| `VILLANI_PROVIDER` | Explicit fallback provider (`openai` or `anthropic`) when `VILLANI_USE_PI_MODEL=false`. |
| `VILLANI_MODEL` | Explicit fallback model. |
| `VILLANI_BASE_URL` | Explicit fallback model endpoint. |
| `VILLANI_API_KEY` | Explicit fallback API key, if needed. |

Explicit Villani provider variables are advanced fallback settings. Normal Pi users should leave them unset so Villani reuses the active Pi model.

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

- **Runtime download failed:** check network access to GitHub Releases, then retry. You can set `VILLANI_COMMAND` to a local executable while troubleshooting.
- **Runtime checksum failed:** the archive was not executed. Clear the runtime cache and retry, or report the release asset mismatch.
- **Local override not found:** if `VILLANI_COMMAND` is set, ensure it points to the executable path in the same environment used to start Pi.
- **No Pi model available:** select/configure a Pi model, or set `VILLANI_USE_PI_MODEL=false` and provide explicit Villani provider variables.
- **Pi auth resolution fails:** confirm the selected Pi model is logged in/configured; auth errors are sanitized before display.
- **Bridge readiness timeout:** run `printf '{"type":"ping","id":"manual-test"}\n' | villani-code bridge --stdio` and confirm it prints `ready` then `pong`.
- **Proxy startup failure:** ensure no local security tool blocks binding to `127.0.0.1` on random ports.
- **Proxy upstream error:** the proxy returns a sanitized HTTP error to Villani instead of a fake empty completion; check Pi model/auth configuration.
- **Approval prompt unavailable:** in headless/non-interactive contexts, approval-required operations are denied for safety. Run Pi interactively or provide an explicit test approval handler.
- **Smoke test:** from this checkout, run `cd integrations/pi-villani && npm install && npm test`, then `printf '{"type":"ping","id":"manual-test"}\n' | villani-code bridge --stdio`.
