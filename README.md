# villani-code

Villani Code is a minimal Claude Code style orchestrator for local Anthropic-compatible LLM servers.

## Install

```bash
python -m pip install -e .
```

## Run

```bash
villani-code run \
  --base-url http://localhost:1234 \
  --model your-model-name \
  --repo /path/to/repo \
  "do a full code review of this repo"
```

Useful flags:
- `--max-tokens 4096`
- `--stream/--no-stream`
- `--thinking '{"type":"enabled"}'`
- `--unsafe`
- `--verbose`
- `--extra-json '{"metadata":{"session":"local"},"context_management":{"type":"auto"}}'`
- `--redact`

Transcripts are saved to `.villani_code/transcripts/`.
