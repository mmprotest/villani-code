import assert from "node:assert/strict";
import test from "node:test";
import type { AssistantMessage, Model } from "@earendil-works/pi-ai";
import { openAIChatToPiContext, PiModelProxy, piAssistantToOpenAIResponse } from "./modelProxy.js";

function fakeModel(): Model<string> {
  return {
    id: "pi-test",
    name: "Pi Test",
    api: "openai-completions",
    provider: "pi",
    baseUrl: "pi://current",
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 4096,
  } as Model<string>;
}

function successMessage(text = "ok"): AssistantMessage {
  return {
    role: "assistant",
    api: "openai-completions",
    provider: "pi",
    model: "pi-test",
    content: [{ type: "text", text }],
    usage: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0, totalTokens: 3, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "stop",
    timestamp: 1700000000000,
  };
}

test("translates OpenAI chat payloads to Pi context", () => {
  const context = openAIChatToPiContext({
    model: "villani-proxy",
    messages: [
      { role: "system", content: "system one" },
      { role: "user", content: "hello" },
      {
        role: "assistant",
        content: "using tool",
        tool_calls: [{ id: "call-1", type: "function", function: { name: "Read", arguments: "{\"path\":\"src/foo.py\"}" } }],
      },
      { role: "tool", tool_call_id: "call-1", name: "Read", content: "file contents" },
    ],
    tools: [{ type: "function", function: { name: "Read", description: "Read file", parameters: { type: "object" } } }],
  });

  assert.equal(context.systemPrompt, "system one");
  assert.equal(context.messages.length, 3);
  assert.equal(context.messages[0].role, "user");
  assert.equal(context.messages[1].role, "assistant");
  assert.equal(context.messages[2].role, "toolResult");
  assert.equal(context.tools?.[0].name, "Read");
});

test("translates Pi assistant tool calls to OpenAI response", () => {
  const message: AssistantMessage = {
    role: "assistant",
    api: "openai-completions",
    provider: "pi",
    model: "m",
    content: [
      { type: "text", text: "I'll inspect it." },
      { type: "toolCall", id: "call-2", name: "Read", arguments: { path: "src/foo.py" } },
    ],
    usage: { input: 3, output: 4, cacheRead: 0, cacheWrite: 0, totalTokens: 7, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "toolUse",
    timestamp: 1700000000000,
  };

  const response = piAssistantToOpenAIResponse(message);
  const choice = (response.choices as Array<any>)[0];
  assert.equal(choice.finish_reason, "tool_calls");
  assert.equal(choice.message.content, "I'll inspect it.");
  assert.equal(choice.message.tool_calls[0].function.name, "Read");
  assert.equal(choice.message.tool_calls[0].function.arguments, '{"path":"src/foo.py"}');
  assert.deepEqual(response.usage, { prompt_tokens: 3, completion_tokens: 4, total_tokens: 7 });
});

test("proxy passes Pi auth options into completion call", async () => {
  let seenOptions: any;
  const proxy = new PiModelProxy({
    model: fakeModel(),
    apiKey: "secret-api-key",
    headers: { Authorization: "Bearer secret-token", "x-provider": "pi" },
    completeFn: async (_model, _context, options) => {
      seenOptions = options;
      return successMessage("auth ok");
    },
  });
  const url = await proxy.start();
  try {
    const response = await fetch(`${url}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "pi-test", messages: [{ role: "user", content: "hello" }] }),
    });
    assert.equal(response.status, 200);
    assert.equal(seenOptions.apiKey, "secret-api-key");
    assert.deepEqual(seenOptions.headers, { Authorization: "Bearer secret-token", "x-provider": "pi" });
  } finally {
    await proxy.stop();
  }
});

test("proxy serves minimal OpenAI chat completions", async () => {
  const proxy = new PiModelProxy({
    model: fakeModel(),
    completeFn: async (_model, context) => successMessage(`saw ${context.messages.length} message`),
  });
  const url = await proxy.start();
  try {
    assert.match(url, /^http:\/\/127\.0\.0\.1:\d+$/);
    const response = await fetch(`${url}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "pi-test", messages: [{ role: "user", content: "hello" }] }),
    });
    assert.equal(response.status, 200);
    const body = await response.json() as any;
    assert.equal(body.choices[0].message.content, "saw 1 message");
  } finally {
    await proxy.stop();
  }
});

test("proxy returns HTTP error for Pi assistant stopReason error", async () => {
  const proxy = new PiModelProxy({
    model: fakeModel(),
    completeFn: async () => ({ ...successMessage(""), content: [], stopReason: "error", errorMessage: "OpenAI API key is required. Bearer secret-token" }),
  });
  const url = await proxy.start();
  try {
    const response = await fetch(`${url}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "pi-test", messages: [{ role: "user", content: "hello" }] }),
    });
    assert.equal(response.status, 502);
    const body = await response.json() as any;
    assert.match(body.error.message, /OpenAI API key is required/);
    assert.doesNotMatch(body.error.message, /secret-token/);
    assert.equal(body.error.type, "upstream_error");
    assert.equal(body.choices, undefined);
  } finally {
    await proxy.stop();
  }
});

test("proxy returns sanitized HTTP error for thrown provider failure", async () => {
  const proxy = new PiModelProxy({
    model: fakeModel(),
    completeFn: async () => { throw new Error("provider failed api_key=super-secret"); },
  });
  const url = await proxy.start();
  try {
    const response = await fetch(`${url}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "pi-test", messages: [{ role: "user", content: "hello" }] }),
    });
    assert.equal(response.status, 502);
    const body = await response.json() as any;
    assert.match(body.error.message, /provider failed/);
    assert.doesNotMatch(body.error.message, /super-secret/);
  } finally {
    await proxy.stop();
  }
});

test("streaming failure does not emit normal completion", async () => {
  const proxy = new PiModelProxy({
    model: fakeModel(),
    completeFn: async () => ({ ...successMessage(""), content: [], stopReason: "error", errorMessage: "upstream down" }),
  });
  const url = await proxy.start();
  try {
    const response = await fetch(`${url}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "pi-test", stream: true, messages: [{ role: "user", content: "hello" }] }),
    });
    assert.equal(response.status, 502);
    const body = await response.text();
    assert.doesNotMatch(body, /\[DONE\]/);
    assert.match(body, /upstream down/);
  } finally {
    await proxy.stop();
  }
});

test("proxy aborts in-flight Pi completion via signal", async () => {
  const controller = new AbortController();
  let completeSignal: AbortSignal | undefined;
  const proxy = new PiModelProxy({
    model: fakeModel(),
    signal: controller.signal,
    completeFn: async (_model, _context, options) => {
      completeSignal = options?.signal;
      await new Promise<void>((resolve) => options?.signal?.addEventListener("abort", () => resolve(), { once: true }));
      return { ...successMessage(""), content: [], stopReason: "aborted" };
    },
  });
  const url = await proxy.start();
  try {
    const pending = fetch(`${url}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "pi-test", messages: [{ role: "user", content: "hello" }] }),
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    controller.abort();
    const response = await pending;
    assert.equal(completeSignal?.aborted, true);
    assert.equal(response.status, 499);
  } finally {
    await proxy.stop();
  }
});

test("proxy serves Villani-compatible streaming chat completions", async () => {
  const proxy = new PiModelProxy({
    model: fakeModel(),
    completeFn: async () => successMessage("streamed final"),
  });
  const url = await proxy.start();
  try {
    const response = await fetch(`${url}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "pi-test", stream: true, messages: [{ role: "user", content: "hello" }] }),
    });
    assert.equal(response.headers.get("content-type")?.startsWith("text/event-stream"), true);
    const body = await response.text();
    assert.match(body, /data: /);
    assert.match(body, /streamed final/);
    assert.match(body, /\[DONE\]/);
  } finally {
    await proxy.stop();
  }
});
