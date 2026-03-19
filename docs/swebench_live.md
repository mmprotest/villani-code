# SWE-bench-Live with the Villani runner

This repository includes a separate `villani_code.swebench_live` harness that runs the project's own Villani runtime against official SWE-bench-Live instances and emits prediction patches in the JSON format expected by the official evaluator.

## What this integration does

- Loads SWE-bench-Live instances from Hugging Face or a local `.jsonl` export.
- Starts a fresh official SWE-bench-Live launch image for each instance.
- Copies the prepared benchmark repo out to a host-side workspace.
- Runs the Villani CLI from an **external Python 3.11+ runtime** against that host-side workspace copy.
- Syncs the edited repo back into the prepared benchmark workspace.
- Captures `git --no-pager diff HEAD --text` and writes predictions in the official JSON shape.
- Optionally writes per-instance sidecar logs with execution metadata.

This is intentionally a **separate harness** from the project's custom benchmark runner. SWE-bench-Live is the outer harness here, and Villani is the inner agent executor operating on the prepared repo contents.

## Why the Villani runtime must be external

SWE-bench-Live task environments can use repository-specific Python versions, including older Python versions that are incompatible with this project. This repo requires Python 3.11+, so the Villani runtime must run from a separate environment where `villani-code` is already installed.

The runner therefore does **not** install `villani-code` inside the task environment, does **not** run `pip install -e ...` in `/testbed`, and does **not** invoke `python -m villani_code.cli` with the task environment's interpreter.

## Important split notes

- `verified` and `lite` are frozen splits intended for more stable comparisons.
- `full` changes over time as the dataset is updated.
- After generating predictions, run the **official SWE-bench-Live evaluator** on the resulting JSON.
- Before serious experiments, sanity-check your denominator by re-running the official gold patches on your machine because some instances can age or break over time.

The official SWE-bench-Live README currently says:
- the setup is `Python >= 3.10`
- the old `python-only` branch is still recommended for fair comparison on `SWE-bench-Live/SWE-bench-Live` (Python-only, NIPS paper version)
- the current `main` branch is more suitable for the newer MultiLang and Windows datasets

## Prerequisites

1. Install this project in a working Python 3.11+ environment.
2. Ensure Docker is available locally, because the driver starts official per-instance launch images.
3. Ensure your chosen model backend is reachable from the external Villani runtime.
4. Ensure the official SWE-bench-Live dataset and evaluator tooling are installed where you plan to evaluate predictions.

## Configuration

The Villani adapter is configured with flags and/or environment variables:

- `--provider` or `VILLANI_SWEBENCH_PROVIDER` / `VILLANI_PROVIDER`
- `--model` or `VILLANI_SWEBENCH_MODEL` / `VILLANI_MODEL`
- `--base-url` or `VILLANI_SWEBENCH_BASE_URL` / `VILLANI_BASE_URL`
- `--api-key` or provider-specific env vars:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `VILLANI_SWEBENCH_API_KEY`
- `--runner-python` or `VILLANI_SWEBENCH_RUNNER_PYTHON`
- `--runner-command-prefix` or `VILLANI_SWEBENCH_RUNNER_COMMAND_PREFIX`
- `--runner-cwd` for an optional external runner cwd

Use **either** `--runner-python` **or** `--runner-command-prefix`.

Examples:

- `--runner-python .venv/bin/python`
- `--runner-python C:\Users\Simon\OneDrive\Documents\Python Scripts\villani-code\.venv\Scripts\python.exe`
- `--runner-command-prefix "python -m villani_code.cli"`

If you omit both runner flags, the driver defaults to the current host interpreter running this repo successfully.

## Path mapping model

The prepared benchmark repo still lives in the task environment at `/testbed` on Linux or `C:\testbed` on Windows. Because the Villani runtime is external, it edits a host-side workspace copy instead. After the run, the driver syncs the host-side repo back into the benchmark workspace before capturing the diff.

If the accessible path differs from `/testbed`, the prompt includes a short note explaining the mapping.

## Running the driver

Example with a local OpenAI-compatible backend on Linux:

```bash
python -m villani_code.swebench_live.run \
  --dataset SWE-bench-Live/SWE-bench-Live \
  --split verified \
  --platform linux \
  --instance-limit 1 \
  --provider openai \
  --model models@q6_k \
  --base-url http://127.0.0.1:1234/v1 \
  --runner-python .venv/bin/python \
  --output artifacts/swebench_live/predictions_local.json \
  --logs-output artifacts/swebench_live/predictions_local.jsonl
```

Windows example using the local virtualenv Python:

```powershell
$env:OPENAI_API_KEY = "dummy"

python -m villani_code.swebench_live.run `
  --dataset SWE-bench-Live/SWE-bench-Live `
  --split verified `
  --platform windows `
  --instance-limit 1 `
  --provider openai `
  --model models@q6_k `
  --base-url http://127.0.0.1:1234/v1 `
  --runner-python .\.venv\Scripts\python.exe `
  --output artifacts\swebench_live\predictions_local.json `
  --logs-output artifacts\swebench_live\predictions_local.jsonl
```

You can also use the script entrypoint:

```bash
villani-code-swebench-live \
  --dataset SWE-bench-Live/SWE-bench-Live \
  --split verified \
  --platform linux \
  --instance-limit 1 \
  --provider openai \
  --model models@q6_k \
  --base-url http://127.0.0.1:1234/v1 \
  --runner-command-prefix "python -m villani_code.cli" \
  --output artifacts/swebench_live/predictions_local.json
```

## Output files

Predictions use the official shape:

```json
{
  "<instance_id>": {
    "model_patch": "<git diff output>"
  }
}
```

Optional sidecar logs record:

- `instance_id`
- `start_timestamp`
- `end_timestamp`
- `exit_code`
- `stdout_path`
- `stderr_path`
- `patch_byte_size`
- `duration_seconds`
- `error_summary`
- `timed_out`

## Official evaluation afterward

After predictions are generated, run the official evaluator from the SWE-bench-Live project, for example:

```bash
python -m evaluation.evaluation \
  --dataset SWE-bench-Live/SWE-bench-Live \
  --split verified \
  --platform linux \
  --patch_dir artifacts/swebench_live/predictions_local.json \
  --output_dir logs/swebench_live_eval \
  --workers 1 \
  --overwrite 0
```

This repo does **not** replace the official evaluator; it only produces prediction patches for it.

## Suggested first run flow

1. Verify the official SWE-bench-Live and RepoLaunch setup on your machine.
2. Sanity-check the evaluator with gold patches to understand the live denominator on your environment.
3. Run a tiny sample with `--instance-limit 1`.
4. Scale up to `verified` or `lite` once the small sample is stable.
