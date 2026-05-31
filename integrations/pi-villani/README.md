# Villani for Pi

Villani is a coding runner extension for [Pi](https://github.com/earendil-works/pi). It runs repository tasks from inside Pi, asks for approval before protected edits or shell commands, and reports the final result in the Pi interface.

## Install in one command

```bash
pi install npm:@mmprotest/pi-villani
```

That is the full Villani installation step.

On first use, the extension automatically downloads the standalone Villani runtime for your platform, verifies its SHA-256 checksum, and caches it locally. You do **not** need to clone the Villani repository, install Python, create a virtual environment, or configure a separate Villani executable.

## Requirements

You need:

- [Pi](https://github.com/earendil-works/pi) installed.
- A model configured and working in Pi.
- A Git repository to run Villani against.
- A supported platform:
  - Windows x64
  - macOS Apple Silicon
  - macOS Intel
  - Linux x64

## Confirm installation

```bash
pi list
```

You should see:

```text
npm:@mmprotest/pi-villani
```

If you see more than one Villani package or a local Villani extension path as well as the npm package, remove the duplicate. A duplicate load can cause Pi to expose the command as `/villani:1` rather than `/villani`.

## Use Villani

Open a terminal in any Git repository and start Pi:

```bash
cd /path/to/your/repository
pi
```

In Pi, run a coding task with `/villani`:

```text
/villani Fix the failing tests and verify the result
```

Other examples:

```text
/villani Add input validation to the parser and add tests
/villani Find the cause of the failing test, make the smallest fix, and run pytest
/villani Add a new endpoint, update tests, and verify the suite
```

To stop a current run:

```text
/villani-abort
```

## What happens during a run

Villani uses the model currently selected in Pi. It can inspect the repository, propose edits, apply patches, and run verification commands.

When Villani requests a protected operation, Pi shows an approval prompt. Typical approvals include:

- Writing or modifying a file.
- Applying a patch.
- Running a shell command such as a test suite.

Review each request before approving it. Villani runs with your normal user permissions inside the current repository.

At the end of a successful run, Pi displays a final Villani result with the task summary and files changed by Villani.

## Using LM Studio

Villani uses Pi's active model, so LM Studio must be configured in Pi first.

### 1. Start the LM Studio server

In LM Studio:

1. Load a coding-capable model.
2. Start the local server.
3. Note the model identifier exposed by the server.

A tested example model identifier is:

```text
villanis/models/qwen3.5-9b-q8_0.gguf
```

The common LM Studio OpenAI-compatible server endpoint is:

```text
http://127.0.0.1:1234/v1
```

### 2. Configure the model in Pi

Create or edit:

- Windows: `%USERPROFILE%\.pi\agent\models.json`
- macOS/Linux: `~/.pi/agent/models.json`

Example configuration:

```json
{
  "providers": {
    "lmstudio": {
      "baseUrl": "http://127.0.0.1:1234/v1",
      "api": "openai-completions",
      "apiKey": "dummy",
      "compat": {
        "supportsDeveloperRole": false,
        "supportsReasoningEffort": false
      },
      "models": [
        {
          "id": "villanis/models/qwen3.5-9b-q8_0.gguf",
          "name": "Qwen 3.5 9B Q8 Local",
          "input": ["text"],
          "reasoning": false,
          "contextWindow": 100000,
          "maxTokens": 16384
        }
      ]
    }
  }
}
```

Replace the model `id` and `name` with the model you loaded in LM Studio.

### 3. Launch Pi using the LM Studio model

```bash
pi --provider lmstudio --model "villanis/models/qwen3.5-9b-q8_0.gguf"
```

Then run:

```text
/villani Fix the failing tests and verify the result
```

### PowerShell setup for the tested LM Studio model

Windows users can create the Pi model configuration with this command:

```powershell
$model = "villanis/models/qwen3.5-9b-q8_0.gguf"
$piDir = Join-Path $HOME ".pi\agent"
$modelsFile = Join-Path $piDir "models.json"

New-Item -ItemType Directory -Force -Path $piDir | Out-Null

$config = @{
    providers = @{
        lmstudio = @{
            baseUrl = "http://127.0.0.1:1234/v1"
            api = "openai-completions"
            apiKey = "dummy"
            compat = @{
                supportsDeveloperRole = $false
                supportsReasoningEffort = $false
            }
            models = @(
                @{
                    id = $model
                    name = "Qwen 3.5 9B Q8 Local"
                    input = @("text")
                    reasoning = $false
                    contextWindow = 100000
                    maxTokens = 16384
                }
            )
        }
    }
} | ConvertTo-Json -Depth 10

[System.IO.File]::WriteAllText(
    $modelsFile,
    $config,
    [System.Text.UTF8Encoding]::new($false)
)

pi --provider lmstudio --model $model
```

## Using another Pi model provider

Villani does not require LM Studio. It reuses the model selected in Pi, including supported cloud providers or other OpenAI-compatible local servers configured in Pi.

Once a normal Pi prompt works with your chosen model, use Villani in the same session:

```text
/villani Implement the requested change and run the relevant tests
```

## Runtime download and cache

On the first Villani run, the extension downloads the runtime for your operating system from the Villani GitHub release assets and verifies it before execution.

Runtime cache locations:

| Platform | Cache location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\pi-villani\runtime\` |
| macOS/Linux | `~/.cache/pi-villani/runtime/` |

To force a clean runtime download on Windows:

```powershell
Remove-Item "$env:LOCALAPPDATA\pi-villani\runtime" -Recurse -Force -ErrorAction SilentlyContinue
```

To force a clean runtime download on macOS or Linux:

```bash
rm -rf ~/.cache/pi-villani/runtime
```

## Update Villani

Install the latest published package update:

```bash
pi update npm:@mmprotest/pi-villani
```

You can also remove and reinstall the package:

```bash
pi remove npm:@mmprotest/pi-villani
pi install npm:@mmprotest/pi-villani
```

## Uninstall Villani

```bash
pi remove npm:@mmprotest/pi-villani
```

Optional: delete the downloaded runtime cache.

Windows PowerShell:

```powershell
Remove-Item "$env:LOCALAPPDATA\pi-villani" -Recurse -Force -ErrorAction SilentlyContinue
```

macOS/Linux:

```bash
rm -rf ~/.cache/pi-villani
```

## Troubleshooting

### `/villani` is missing

Check that Pi installed the extension:

```bash
pi list
```

You should see `npm:@mmprotest/pi-villani`.

Reinstall if required:

```bash
pi remove npm:@mmprotest/pi-villani
pi install npm:@mmprotest/pi-villani
```

### Pi shows `/villani:1`

Pi has loaded Villani more than once, usually because both a local development path and the npm package are installed.

```bash
pi list
```

Remove every old or local Villani entry, then install only the public package:

```bash
pi install npm:@mmprotest/pi-villani
```

### The model works in LM Studio but not in Pi

First test the model with a normal Pi prompt:

```bash
pi --provider lmstudio --model "your-model-id"
```

Then ask:

```text
Reply with exactly: hello
```

If the normal Pi prompt fails, fix the Pi or LM Studio model configuration before testing Villani.

Check:

- The LM Studio server is running.
- The `baseUrl` includes `/v1`.
- The model ID exactly matches the model shown by LM Studio.
- `models.json` is valid JSON saved as UTF-8.

### Runtime download fails

Check access to GitHub Releases and retry. To remove a partially cached runtime, clear the runtime cache using the commands above and launch Villani again.

### Checksum verification fails

Villani will not execute a runtime archive that does not match its published checksum. Clear the cache and retry. If the error persists, report the runtime asset or checksum mismatch.

### Approval prompt does not appear

Run Pi interactively in a terminal. Approval-required operations are denied when no usable Pi confirmation UI is available.

### A run needs to be stopped

Use:

```text
/villani-abort
```

### Debugging output

For troubleshooting only, enable extension diagnostics before launching Pi.

Windows PowerShell:

```powershell
$env:VILLANI_PI_DEBUG = "1"
pi
```

macOS/Linux:

```bash
VILLANI_PI_DEBUG=1 pi
```

Turn debug output off after troubleshooting.

Windows PowerShell:

```powershell
Remove-Item Env:VILLANI_PI_DEBUG -ErrorAction SilentlyContinue
```

macOS/Linux:

```bash
unset VILLANI_PI_DEBUG
```

## Advanced development override

Normal users do not need this.

Developers can bypass automatic runtime download and point the extension at a local Villani executable using `VILLANI_COMMAND`:

Windows PowerShell:

```powershell
$env:VILLANI_COMMAND = "C:\path\to\villani-code\.venv\Scripts\villani-code.exe"
pi
```

macOS/Linux:

```bash
VILLANI_COMMAND="/path/to/villani-code/.venv/bin/villani-code" pi
```

## Security notes

- Pi packages execute code with your normal user permissions.
- Villani can read and modify files in the repository where it is run.
- Review approval prompts before allowing edits or commands.
- The downloaded standalone runtime is verified using the published SHA-256 checksum before it is executed.
- In normal operation, Villani uses the active Pi model through a temporary local proxy bound to `127.0.0.1`.

## Reference links

- Pi package installation documentation: <https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md>
- Pi custom model configuration documentation: <https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md>
- Villani Code repository: <https://github.com/mmprotest/villani-code>
- Villani Pi npm package: <https://www.npmjs.com/package/@mmprotest/pi-villani>
