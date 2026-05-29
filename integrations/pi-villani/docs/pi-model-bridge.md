# Pi-backed model bridge

The Pi extension reuses Pi's active model by default through a temporary localhost OpenAI-compatible proxy. This avoids asking users to configure provider/model/base URL/API key twice.

```text
Villani Runner OpenAIClient
  -> http://127.0.0.1:<random-port>/v1/chat/completions
  -> pi-villani local proxy
  -> @earendil-works/pi-ai complete()
  -> user's configured Pi provider/model/auth
```

## Villani API surface implemented

Villani's `OpenAIClient` calls:

- `POST /v1/chat/completions`
- payload fields: `model`, `messages`, `max_tokens`, `stream`, optional `tools`, optional `stream_options`
- OpenAI function tool calls and tool-result messages
- non-streaming JSON responses
- SSE streaming responses using `data: ...` lines followed by `data: [DONE]`

The proxy implements exactly that path. It translates:

- OpenAI `system` messages into Pi `systemPrompt`
- OpenAI user messages into Pi user messages
- OpenAI assistant tool calls into Pi `toolCall` content
- OpenAI tool messages into Pi `toolResult` messages
- OpenAI function tool definitions into Pi `Tool` definitions
- Pi text/tool-call assistant content back into OpenAI `message.content` and `message.tool_calls`

## Runtime behavior

- One proxy is started per active `/villani` run.
- The proxy binds to `127.0.0.1` only.
- The OS chooses a random available port.
- The proxy is stopped on success, failure, abort and subprocess startup failure.
- The Python child receives no Pi provider credentials.

## Configuration precedence

1. Default: use Pi's active model through the local proxy.
2. If `VILLANI_USE_PI_MODEL=false`, skip the proxy and use explicit `VILLANI_PROVIDER`, `VILLANI_MODEL`, `VILLANI_BASE_URL` and optional `VILLANI_API_KEY`.
3. If Pi has no active model and explicit fallback is not enabled, `/villani` fails with a configuration message rather than guessing.

## Streaming limitation

The current proxy uses `@earendil-works/pi-ai` `complete()` and emits the completed assistant response as a single OpenAI-compatible SSE chunk when Villani asks for streaming. This exercises Villani's streaming client path but does not provide token-by-token streaming. True token streaming can be added later by translating Pi `stream()` events to OpenAI SSE chunks.
