# Pi-backed model bridge

Milestone 2 adds a temporary localhost proxy owned by the Pi extension so Villani can reuse Pi's currently configured model without receiving user provider credentials.

```text
Villani Runner
  -> http://127.0.0.1:<random-port>/v1/chat/completions
  -> pi-villani local proxy
  -> @earendil-works/pi-ai complete()
  -> user's configured Pi provider/model/auth
```

## Current implementation

- The proxy starts only for one `/villani` command and is stopped in the command cleanup path.
- It binds to `127.0.0.1` on a random available port.
- The extension passes `provider: "openai"`, the temporary `base_url`, and the active Pi model id to Villani.
- No `VILLANI_API_KEY` is passed when the proxy path is used.
- The proxy implements the minimal `/v1/chat/completions` surface Villani uses through `OpenAIClient`:
  - `messages`
  - `tools` / function tool definitions
  - `max_tokens`
  - `temperature`
  - `stream`
- The proxy translates OpenAI chat messages/tool calls/tool results into Pi `Context`/`Tool` shapes, calls `complete()`, and translates the Pi assistant message back into an OpenAI-compatible response.
- Streaming is compatibility streaming: Pi is called via `complete()` first, then the final assistant message is emitted as a single SSE chunk. This preserves Villani's streaming client path without claiming token-by-token streaming.

## Fallback behavior

If any explicit Villani model environment variable is set (`VILLANI_PROVIDER`, `VILLANI_MODEL`, `VILLANI_BASE_URL`, or `VILLANI_API_KEY`), the extension skips the Pi proxy and uses the explicit configuration exactly as before. This keeps local OpenAI-compatible servers and other custom Villani provider setups working.

## Future improvements

- Switch from `complete()` to true Pi token/event streaming if the active Pi provider exposes a stable stream suitable for OpenAI SSE translation.
- Add broader compatibility for `/v1/messages` only if Villani needs Anthropic-compatible direct proxying in a future client path.
- Surface proxy diagnostics in Pi's UI without logging request bodies or credentials.
