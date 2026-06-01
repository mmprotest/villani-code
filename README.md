# Villani Code

**A terminal-first coding agent runtime built to make small local models complete real repository work.**

Villani Code runs coding tasks inside a repository, gives the model tools to inspect files, modify code and execute verification, and keeps the work oriented toward a passing result.

It is designed for local and constrained backends, where the runtime has to do more than pass messages to a powerful hosted model. Villani focuses the agent on the loop that matters:

1. inspect the repository and task;
2. identify the smallest useful change;
3. edit or run commands;
4. verify the result;
5. recover when verification fails.

Use Villani directly from the command line, or run it inside [Pi](https://github.com/earendil-works/pi) through the `@mmprotest/pi-villani` extension.

## Benchmark results

Using **Qwen3.5-9B** across the full **Terminal-Bench 2.0** task suite, Villani Code achieved **92 verified completions from 445 clean attempts**, a **20.67%** score.

That score would place **Villani Code + Qwen3.5-9B at #126** on the current Terminal-Bench 2.0 leaderboard, above **Gemini CLI + Gemini 2.5 Pro** and **Bash Agent + TermiGen-32B**. The published **little-coder + Qwen3.5-9B** entry scores **9.2%**. Villani scores **2.25x higher with the same model class**.

![Villani Code projected Terminal-Bench 2.0 leaderboard position](docs/assets/villani_terminal_bench_2_leaderboard_position.png)

[Read the full technical report](docs/Villani_Code_9B_Terminal_Bench_Technical_Report_Leaderboard.pdf)

## Install Villani Code

### Requirements

- Python 3.11 or later
- A Git repository to work in
- An OpenAI-compatible model endpoint

### Install

Clone the repository and install Villani:

```bash
git clone https://github.com/mmprotest/villani-code.git
cd villani-code
pip install .[tui]
```

For headless CLI use only:

```bash
pip install .
```

For development:

```bash
pip install .[dev]
```

### Run against a local model

Start an OpenAI-compatible local server, then point Villani at it:

```bash
villani-code interactive \
  --base-url http://127.0.0.1:1234 \
  --model your-model-id \
  --repo /path/to/repository
```

Run a single task:

```bash
villani-code run "Fix the failing tests and verify the result." \
  --base-url http://127.0.0.1:1234 \
  --model your-model-id \
  --repo /path/to/repository
```

## Install Villani in Pi

Villani is available as a Pi extension. It runs repository tasks from inside Pi, uses the model selected in Pi, requests approval before protected edits or shell commands, and reports the final result in the Pi interface.

### Requirements

- [Pi](https://github.com/earendil-works/pi) installed
- A model configured and working in Pi
- A Git repository
- Windows x64, macOS Apple Silicon, macOS Intel, or Linux x64

### Install the extension

```bash
pi install npm:@mmprotest/pi-villani
```

On first use, the extension downloads the Villani runtime for your platform, verifies its SHA-256 checksum, and caches it locally. You do not need to clone this repository or install Python to use Villani from Pi.

Confirm that Pi loaded the extension:

```bash
pi list
```

You should see:

```text
npm:@mmprotest/pi-villani
```

### Run Villani in Pi

Open a terminal in a Git repository and launch Pi:

```bash
cd /path/to/repository
pi
```

Then invoke Villani:

```text
/villani Fix the failing tests and verify the result
```

Stop an active run with:

```text
/villani-abort
```


### Update or uninstall the Pi extension

Update:

```bash
pi update npm:@mmprotest/pi-villani
```

Uninstall:

```bash
pi remove npm:@mmprotest/pi-villani
```

### Pi troubleshooting

If `/villani` does not appear, run `pi list` and reinstall the package.

If Pi shows `/villani:1`, remove duplicate local or npm extension installs, then install only:

```bash
pi install npm:@mmprotest/pi-villani
```

If a local model fails, test it with a normal Pi prompt before running Villani and confirm that the endpoint includes `/v1` and the model ID matches the configured model.
