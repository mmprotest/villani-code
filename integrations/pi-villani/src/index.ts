import { randomUUID } from "node:crypto";
import type { Model } from "@earendil-works/pi-ai";
import { BridgeEvent, RunCommand, VillaniMode } from "./protocol.js";
import { VillaniBridgeProcess } from "./process.js";
import { PiModelProxy } from "./modelProxy.js";
import { PiLikeOutput, renderEvent } from "./render.js";

interface PiUI {
  notify?: (message: string, level?: "info" | "warn" | "error") => void;
  setStatus?: (key: string, message: string) => void;
  setWidget?: (key: string, lines: string[]) => void;
}

interface PiLikeCommandContext {
  cwd?: string;
  model?: Model<string>;
  signal?: AbortSignal;
  ui?: PiUI;
  workspace?: { cwd?: string; rootPath?: string; folders?: Array<{ path?: string; uri?: { fsPath?: string } }> };
}

interface PiLikeAPI {
  registerCommand?: (
    name: string,
    options: { description: string; handler: (args?: string, ctx?: PiLikeCommandContext) => unknown } | ((args?: unknown, ctx?: PiLikeCommandContext) => unknown),
  ) => { dispose?: () => void } | void;
}

interface LegacyPiLikeContext extends PiLikeCommandContext {
  commands?: { registerCommand?: PiLikeAPI["registerCommand"] };
  output?: PiLikeOutput;
  subscriptions?: Array<{ dispose?: () => void }>;
}

export default function villaniPiExtension(pi: PiLikeAPI): void {
  activate(pi as LegacyPiLikeContext);
}

export function activate(context: LegacyPiLikeContext | PiLikeAPI): void {
  const registrar = ("commands" in context ? context.commands?.registerCommand : undefined) ?? (context as PiLikeAPI).registerCommand;
  if (!registrar) {
    throw new Error("Pi command registration API was not found; update src/index.ts for the installed Pi SDK.");
  }
  const disposable = registrar("villani", {
    description: "Delegate a coding task to Villani Code",
    handler: async (args?: string, commandContext?: PiLikeCommandContext) => runVillaniCommand({ ...(context as LegacyPiLikeContext), ...commandContext }, args),
  });
  if (disposable && "subscriptions" in context && context.subscriptions) context.subscriptions.push(disposable);
}

export function deactivate(): void {
  // Nothing persistent to clean up; per-run bridge/proxy processes are stopped in finally blocks.
}

export async function runVillaniCommand(context: LegacyPiLikeContext, args?: unknown): Promise<void> {
  const task = extractTask(args);
  const output = context.output ?? uiOutput(context.ui) ?? console;
  if (!task) throw new Error("Usage: /villani <task>");
  const repo = resolveWorkspace(context);
  const runId = randomUUID();
  const explicitConfig = hasExplicitVillaniModelConfig();
  const proxy = !explicitConfig && context.model ? new PiModelProxy({ model: context.model, signal: context.signal }) : undefined;
  const proxyUrl = proxy ? await proxy.start() : undefined;
  const bridge = new VillaniBridgeProcess({ command: process.env.VILLANI_COMMAND || "villani", cwd: repo });
  let finished = false;
  let resolveFinal!: () => void;
  const finalEvent = new Promise<void>((resolve) => { resolveFinal = resolve; });
  try {
    bridge.onEvent((event: BridgeEvent) => {
      renderEvent(event, output);
      if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_aborted") {
        finished = true;
        resolveFinal();
      }
    });
    if (context.signal) {
      context.signal.addEventListener("abort", () => {
        try { bridge.abort(runId); } catch { /* ignore cancellation races */ }
      }, { once: true });
    }
    await bridge.waitUntilReady();
    const command: RunCommand = {
      type: "run",
      id: runId,
      task,
      repo,
      mode: (process.env.VILLANI_MODE as VillaniMode | undefined) || "runner",
      config: buildRunConfig(proxyUrl, context.model),
    };
    bridge.send(command);
    await Promise.race([
      finalEvent,
      bridge.waitForExit().then((code) => {
        if (!finished) throw new Error(`Villani bridge exited before final event with code ${code}. stderr: ${bridge.stderr()}`);
      }),
    ]);
  } finally {
    if (!finished) {
      try { bridge.abort(runId); } catch { /* ignore cleanup send errors */ }
    }
    bridge.kill();
    await proxy?.stop();
  }
}

function buildRunConfig(proxyUrl: string | undefined, model: Model<string> | undefined): RunCommand["config"] {
  if (proxyUrl) {
    return {
      provider: "openai",
      model: model?.id ?? "pi-current-model",
      base_url: proxyUrl,
    };
  }
  return {
    provider: process.env.VILLANI_PROVIDER,
    model: process.env.VILLANI_MODEL,
    base_url: process.env.VILLANI_BASE_URL,
    api_key: process.env.VILLANI_API_KEY,
  };
}

function hasExplicitVillaniModelConfig(): boolean {
  return Boolean(process.env.VILLANI_PROVIDER || process.env.VILLANI_MODEL || process.env.VILLANI_BASE_URL || process.env.VILLANI_API_KEY);
}

function uiOutput(ui: PiUI | undefined): PiLikeOutput | undefined {
  if (!ui) return undefined;
  return {
    info: (message: string) => ui.notify?.(message, "info") ?? ui.setStatus?.("villani", message),
    warn: (message: string) => ui.notify?.(message, "warn") ?? ui.setStatus?.("villani", message),
    error: (message: string) => ui.notify?.(message, "error") ?? ui.setStatus?.("villani", message),
    markdown: (message: string) => ui.setWidget?.("villani", message.split("\n")) ?? ui.notify?.(message, "info"),
  };
}

function extractTask(args: unknown): string {
  if (typeof args === "string") return args.trim();
  if (args && typeof args === "object" && "text" in args) return String((args as { text?: unknown }).text ?? "").trim();
  if (Array.isArray(args)) return args.join(" ").trim();
  return "";
}

function resolveWorkspace(context: LegacyPiLikeContext): string {
  const workspace = context.workspace;
  return context.cwd || workspace?.cwd || workspace?.rootPath || workspace?.folders?.[0]?.path || workspace?.folders?.[0]?.uri?.fsPath || process.cwd();
}
