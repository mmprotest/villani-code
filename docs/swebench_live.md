# SWE-bench-Live with the Villani runner

This repository now includes a separate `villani_code.swebench_live` harness that runs the project's own Villani runtime against official SWE-bench-Live instances and emits prediction patches in the JSON format expected by the official evaluator.

## What this integration does

- Loads SWE-bench-Live instances from Hugging Face or a local `.jsonl` export.
- Starts a fresh official SWE-bench-Live launch image for each instance.
- Installs this repository's Villani runtime inside that prepared environment.
- Runs the Villani CLI in one-shot mode against the prepared repo at `/testbed` on Linux.
- Captures `git --no-pager diff HEAD --text` and writes predictions in the official JSON shape.
- Optionally writes per-instance sidecar logs with execution metadata.

This is intentionally a **separate harness** from the project's custom benchmark runner. SWE-bench-Live is the outer harness here, and Villani is treated as the inner agent executor operating inside the official prepared instance environment.

## Important split notes

- `verified` and `lite` are frozen splits intended for more stable comparisons.
- `full` changes over time as the dataset is updated.
- After generating predictions, run the **official SWE-bench-Live evaluator** on the resulting JSON.
- Before serious experiments, sanity-check your denominator by re-running the official gold patches on your machine because some instances can age or break over time.

## Prerequisites

1. Install this project.
2. Install the official SWE-bench-Live dataset tooling you plan to use.
3. Ensure Docker is available locally, because the driver starts official per-instance launch images.
4. Make sure the model provider credentials are available either by flags or environment variables.

For the upstream setup, the official SWE-bench-Live repository documents installing the dataset package and RepoLaunch. The evaluator remains external to this repo.

## Configuration

The Villani adapter is configured with flags and/or environment variables:

- `--provider` or `VILLANI_SWEBENCH_PROVIDER` / `VILLANI_PROVIDER`
- `--model` or `VILLANI_SWEBENCH_MODEL` / `VILLANI_MODEL`
- `--base-url` or `VILLANI_SWEBENCH_BASE_URL` / `VILLANI_BASE_URL`
- `--api-key` or provider-specific env vars:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `VILLANI_SWEBENCH_API_KEY`

If you omit `--api-key`, the runner forwards the provider-specific environment variable into the prepared instance.

## Running the driver

Example:

```bash
python -m villani_code.swebench_live.run \
  --dataset SWE-bench-Live/SWE-bench-Live \
  --split verified \
  --platform linux \
  --instance-limit 5 \
  --provider openai \
  --model gpt-4.1-mini \
  --base-url http://127.0.0.1:1234/v1 \
  --output artifacts/swebench_live/predictions_villani.json \
  --logs-output artifacts/swebench_live/predictions_villani.jsonl
```

There is also an entrypoint script:

```bash
villani-code-swebench-live \
  --dataset SWE-bench-Live/SWE-bench-Live \
  --split verified \
  --platform linux \
  --instance-limit 5 \
  --provider anthropic \
  --model claude-sonnet-4-5 \
  --output artifacts/swebench_live/predictions_villani.json
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
  --patch_dir artifacts/swebench_live/predictions_villani.json \
  --output_dir logs/swebench_live_eval \
  --workers 1 \
  --overwrite 0
```

This repo does **not** replace the official evaluator; it only produces prediction patches for it.

## Suggested first run flow

1. Verify the official SWE-bench-Live and RepoLaunch setup on your machine.
2. Sanity-check the evaluator with gold patches to understand the live denominator on your environment.
3. Run a tiny sample with `--instance-limit 1` or `--instance-limit 5`.
4. Scale up to `verified` or `lite` once the small sample is stable.

## Assumptions

- The driver assumes the official per-instance Docker image contains the prepared repository at `/testbed` for Linux.
- A small fraction of official images may place the actual git repo one level deeper; the driver probes for that case and uses the discovered git root.
- Prediction scoring depends only on the resulting git diff, not on model text output.
