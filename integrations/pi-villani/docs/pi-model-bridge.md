# Future Pi-backed model bridge

Current milestone: the Pi extension launches Villani Code and passes explicit Villani model configuration from environment variables (`VILLANI_PROVIDER`, `VILLANI_MODEL`, `VILLANI_BASE_URL`, `VILLANI_API_KEY`). Pi remains the UI/shell, but Villani still talks directly to its configured model provider.

Future milestone: add a temporary local OpenAI- or Anthropic-compatible proxy owned by the Pi extension:

```text
Villani Runner
  -> local temporary OpenAI/Anthropic-compatible endpoint
  -> Pi AI provider/model APIs
  -> user's existing Pi authentication
```

Benefits:

- Pi users do not configure credentials twice.
- Villani can keep using its existing provider/client abstractions.
- The integration remains a bridge rather than a port of Villani's runner.

Implementation notes for that future work:

- Start the proxy only for the lifetime of one `/villani` run.
- Bind to localhost on an ephemeral port.
- Translate only stable chat/message API fields needed by Villani.
- Preserve the same bridge JSONL protocol so cancellation, progress rendering, and final summaries do not change.
- Do not implement a partial proxy until the Pi AI package API is available and testable.
