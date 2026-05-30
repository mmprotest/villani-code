import { createServer, IncomingMessage, Server, ServerResponse } from "node:http";
import { AddressInfo } from "node:net";
import { complete } from "@earendil-works/pi-ai";
import type { AssistantMessage, Context, Message, Model, ProviderStreamOptions, Tool, Usage } from "@earendil-works/pi-ai";

export interface OpenAIMessage {
  role: "system" | "user" | "assistant" | "tool";
  content?: string | null;
  tool_call_id?: string;
  name?: string;
  tool_calls?: Array<{
    id?: string;
    type?: "function";
    function?: { name?: string; arguments?: string };
  }>;
}

export interface OpenAIChatCompletionRequest {
  model?: string;
  messages?: OpenAIMessage[];
  tools?: Array<{
    type?: "function";
    function?: { name?: string; description?: string; parameters?: Record<string, unknown> };
  }>;
  max_tokens?: number;
  temperature?: number;
  stream?: boolean;
}

export type PiCompleteFunction = (model: Model<string>, context: Context, options?: ProviderStreamOptions) => Promise<AssistantMessage>;

export interface PiModelProxyOptions {
  model: Model<string>;
  apiKey?: string;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
  completeFn?: PiCompleteFunction;
}

export class PiModelProxy {
  private server?: Server;
  private url?: string;

  constructor(private readonly options: PiModelProxyOptions) {}

  async start(): Promise<string> {
    if (this.url) return this.url;
    if (this.options.signal?.aborted) throw new Error("Pi model proxy startup aborted");
    this.server = createServer((req, res) => {
      void this.handle(req, res);
    });
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        this.server?.close();
        reject(abortError());
      };
      this.options.signal?.addEventListener("abort", abort, { once: true });
      this.server?.once("error", reject);
      this.server?.listen(0, "127.0.0.1", () => {
        this.options.signal?.removeEventListener("abort", abort);
        resolve();
      });
    });
    const address = this.server.address() as AddressInfo;
    this.url = `http://127.0.0.1:${address.port}`;
    return this.url;
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = undefined;
    this.url = undefined;
    if (!server) return;
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    try {
      if (req.method !== "POST" || !req.url?.startsWith("/v1/chat/completions")) {
        writeJson(res, 404, { error: { message: "Pi Villani proxy only supports POST /v1/chat/completions", type: "not_found" } });
        return;
      }
      if (this.options.signal?.aborted) throw abortError();
      const payload = JSON.parse(await readBody(req)) as OpenAIChatCompletionRequest;
      const context = openAIChatToPiContext(payload);
      const assistant = await this.complete(context, payload);
      if (this.options.signal?.aborted || assistant.stopReason === "aborted") throw abortError();
      if (assistant.stopReason === "error") {
        throw new UpstreamModelError(assistant.errorMessage ?? "Pi model request failed");
      }
      if (payload.stream) {
        writeOpenAIStream(res, assistant);
      } else {
        writeJson(res, 200, piAssistantToOpenAIResponse(assistant));
      }
    } catch (error) {
      writeProxyError(res, error);
    }
  }

  private complete(context: Context, payload: OpenAIChatCompletionRequest): Promise<AssistantMessage> {
    const completeFn = this.options.completeFn ?? complete;
    return completeFn(this.options.model, context, {
      apiKey: this.options.apiKey,
      headers: this.options.headers,
      maxTokens: payload.max_tokens,
      temperature: payload.temperature,
      signal: this.options.signal,
      timeoutMs: this.options.timeoutMs,
    });
  }
}

class UpstreamModelError extends Error {
  readonly status = 502;
  readonly type = "upstream_error";
}

class AbortRequestError extends Error {
  readonly status = 499;
  readonly type = "aborted";
}

export function openAIChatToPiContext(request: OpenAIChatCompletionRequest): Context {
  const messages = request.messages ?? [];
  const systemPrompt = messages
    .filter((message) => message.role === "system" && message.content)
    .map((message) => String(message.content))
    .join("\n\n") || undefined;
  const converted: Message[] = [];
  for (const message of messages) {
    if (message.role === "system") continue;
    const timestamp = Date.now();
    if (message.role === "user") {
      converted.push({ role: "user", content: String(message.content ?? ""), timestamp });
      continue;
    }
    if (message.role === "tool") {
      converted.push({
        role: "toolResult",
        toolCallId: String(message.tool_call_id ?? ""),
        toolName: String(message.name ?? ""),
        content: [{ type: "text", text: String(message.content ?? "") }],
        isError: false,
        timestamp,
      });
      continue;
    }
    if (message.role === "assistant") {
      const content: AssistantMessage["content"] = [];
      if (message.content) content.push({ type: "text", text: String(message.content) });
      for (const toolCall of message.tool_calls ?? []) {
        const fn = toolCall.function ?? {};
        content.push({
          type: "toolCall",
          id: String(toolCall.id ?? `tool-${content.length}`),
          name: String(fn.name ?? ""),
          arguments: parseArguments(fn.arguments),
        });
      }
      converted.push({
        role: "assistant",
        content,
        api: "openai-completions",
        provider: "pi",
        model: String(request.model ?? "pi"),
        usage: zeroUsage(),
        stopReason: content.some((block) => block.type === "toolCall") ? "toolUse" : "stop",
        timestamp,
      });
    }
  }
  const tools: Tool[] = (request.tools ?? [])
    .filter((tool) => tool.type === "function" && tool.function?.name)
    .map((tool) => ({
      name: String(tool.function?.name ?? ""),
      description: String(tool.function?.description ?? ""),
      parameters: (tool.function?.parameters ?? { type: "object", properties: {} }) as Tool["parameters"],
    }));
  return { systemPrompt, messages: converted, ...(tools.length ? { tools } : {}) };
}

export function piAssistantToOpenAIResponse(message: AssistantMessage): Record<string, unknown> {
  if (message.stopReason === "error") {
    throw new UpstreamModelError(message.errorMessage ?? "Pi model request failed");
  }
  if (message.stopReason === "aborted") throw abortError();
  const text = message.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("\n\n");
  const toolCalls = message.content
    .filter((block) => block.type === "toolCall")
    .map((block, index) => ({
      id: block.id || `call_${index}`,
      type: "function",
      function: { name: block.name, arguments: JSON.stringify(block.arguments ?? {}) },
    }));
  return {
    id: message.responseId ?? `pi-villani-${message.timestamp}`,
    object: "chat.completion",
    created: Math.floor(message.timestamp / 1000),
    model: message.responseModel ?? message.model,
    choices: [
      {
        index: 0,
        message: {
          role: "assistant",
          content: text || null,
          ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
        },
        finish_reason: toOpenAIFinishReason(message.stopReason),
      },
    ],
    usage: toOpenAIUsage(message.usage),
  };
}

function writeOpenAIStream(res: ServerResponse, message: AssistantMessage): void {
  const response = piAssistantToOpenAIResponse(message);
  const choice = (response.choices as Array<Record<string, unknown>>)[0];
  const fullMessage = choice.message as Record<string, unknown>;
  res.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  res.write(`data: ${JSON.stringify({
    id: response.id,
    object: "chat.completion.chunk",
    created: response.created,
    model: response.model,
    choices: [{ index: 0, delta: fullMessage, finish_reason: choice.finish_reason }],
    usage: response.usage,
  })}\n\n`);
  res.write("data: [DONE]\n\n");
  res.end();
}

function toOpenAIFinishReason(reason: AssistantMessage["stopReason"]): string {
  if (reason === "toolUse") return "tool_calls";
  if (reason === "length") return "length";
  return "stop";
}

function toOpenAIUsage(usage: Usage): Record<string, number> {
  return {
    prompt_tokens: usage.input,
    completion_tokens: usage.output,
    total_tokens: usage.totalTokens || usage.input + usage.output,
  };
}

function zeroUsage(): Usage {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function parseArguments(value: unknown): Record<string, unknown> {
  if (typeof value !== "string") return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  return Buffer.concat(chunks).toString("utf8");
}

function writeJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
}

function writeProxyError(res: ServerResponse, error: unknown): void {
  const err = error as { status?: number; type?: string; message?: string; name?: string };
  const aborted = err.type === "aborted" || err.name === "AbortError";
  const status = aborted ? 499 : err.status ?? 502;
  const type = aborted ? "aborted" : err.type ?? "upstream_error";
  const prefix = aborted ? "Pi model request was aborted" : "Villani model request failed through Pi";
  writeJson(res, status, {
    error: {
      message: `${prefix}: ${sanitizeErrorMessage(err.message ?? String(error))}`,
      type,
    },
  });
}

function abortError(): AbortRequestError {
  return new AbortRequestError("request aborted");
}

function sanitizeErrorMessage(message: string): string {
  return message
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [redacted]")
    .replace(/api[_-]?key[=:]\s*[^\s,;]+/gi, "api_key=[redacted]")
    .slice(0, 500);
}
