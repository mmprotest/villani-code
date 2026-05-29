import { randomUUID } from "node:crypto";
import type { ExtensionAPI, ExtensionCommandContext, ExtensionUIContext } from "@earendil-works/pi-coding-agent";
import type { Model } from "@earendil-works/pi-ai";
import { BridgeEvent, RunCommand, VillaniMode } from "./protocol.js";
import { startVillaniBridgeProcess, VillaniBridgeProcess } from "./process.js";
import { PiModelProxy } from "./modelProxy.js";
import { PiLikeOutput, renderEvent } from "./render.js";

type ActiveRunPhase = "starting" | "running" | "aborting" | "completed";

interface ActiveVillaniRun {
  id: string;
  repo: string;
  phase: ActiveRunPhase;
  abortController: AbortController;
  bridge?: VillaniBridgeProcess;
  proxy?: PiModelProxy;
  done: Promise<void>;
  resolveDone: () => void;
}

let activeRun: ActiveVillaniRun | undefined;

export default function villaniPiExtension(pi: ExtensionAPI): void {
  pi.registerCommand("villani", {
    description: "Delegate a repository coding task to Villani Code",
    handler: async (args: string, ctx: ExtensionCommandContext) => runVillaniCommand(args, ctx),
  });
  pi.registerCommand("villani-abort", {
    description: "Abort the active Villani Code run",
    handler: async (_args: string, ctx: ExtensionCommandContext) => abortVillaniRun(ctx),
  });
}

export async function runVillaniCommand(args: string, ctx: ExtensionCommandContext): Promise<void> {
  const task = args.trim();
  const output = uiOutput(ctx.ui);
  if (!task) {
    output.warn?.("Usage: /villani <task>");
    return;
  }
  if (activeRun) {
    output.warn?.(`Villani is already running in ${activeRun.repo}. Use /villani-abort to stop it before starting another run.`);
    return;
  }

  const repo = ctx.cwd || process.cwd();
  const runId = randomUUID();
  const abortController = new AbortController();
  let resolveDone!: () => void;
  const done = new Promise<void>((resolve) => { resolveDone = resolve; });
  const run: ActiveVillaniRun = { id: runId, repo, phase: "starting", abortController, done, resolveDone };
  activeRun = run;

  let finished = false;
  let resolveFinal!: () => void;
  const finalEvent = new Promise<void>((resolve) => { resolveFinal = resolve; });
  ctx.signal?.addEventListener("abort", () => {
    void abortActiveRun("Pi command cancellation requested");
  }, { once: true });

  try {
    const explicitConfig = useExplicitVillaniConfig();
    const model = ctx.model as Model<string> | undefined;
    if (!explicitConfig && !model) {
      throw new Error("Villani could not start: no active Pi model is selected. Select a model in Pi, or set VILLANI_USE_PI_MODEL=false and configure Villani explicitly.");
    }
    if (abortController.signal.aborted) throw new Error("Villani run cancelled during startup.");

    const auth = !explicitConfig && model ? await resolveModelAuth(ctx, model) : undefined;
    if (abortController.signal.aborted) throw new Error("Villani run cancelled during startup.");

    run.proxy = !explicitConfig && model ? new PiModelProxy({ model, apiKey: auth?.apiKey, headers: auth?.headers, signal: abortController.signal }) : undefined;
    const proxyUrl = run.proxy ? await run.proxy.start() : undefined;
    if (abortController.signal.aborted) throw new Error("Villani run cancelled during startup.");

    run.bridge = await startVillaniBridgeProcess({ command: process.env.VILLANI_COMMAND, cwd: repo, signal: abortController.signal });
    run.phase = "running";
    run.bridge.onEvent((event: BridgeEvent) => {
      if (abortController.signal.aborted && event.type === "run_completed") return;
      renderEvent(event, output);
      if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_aborted") {
        finished = event.type === "run_completed";
        resolveFinal();
      }
      if (event.type === "error") output.error?.(`Villani bridge error: ${event.error}`);
    });

    const command: RunCommand = {
      type: "run",
      id: runId,
      task,
      repo,
      mode: (process.env.VILLANI_MODE as VillaniMode | undefined) || "runner",
      config: buildRunConfig(proxyUrl, model),
    };
    run.bridge.send(command);
    await Promise.race([
      finalEvent,
      run.bridge.waitForExit().then((code) => {
        if (!finished && !abortController.signal.aborted) throw new Error(`Villani bridge exited before a final event with code ${code}. ${run.bridge?.stderr() ?? ""}`.trim());
      }),
    ]);
  } catch (error) {
    if (abortController.signal.aborted) output.warn?.(!run.bridge ? "Villani run cancelled during startup." : "Villani run cancelled.");
    else output.error?.(error instanceof Error ? error.message : String(error));
  } finally {
    run.phase = "completed";
    if (!finished && run.bridge && !abortController.signal.aborted) {
      try { run.bridge.abort(runId); } catch { /* ignore cleanup races */ }
    }
    run.bridge?.kill();
    await run.proxy?.stop();
    if (activeRun?.id === runId) activeRun = undefined;
    resolveDone();
  }
}

export async function abortVillaniRun(ctx: ExtensionCommandContext): Promise<void> {
  const output = uiOutput(ctx.ui);
  if (!activeRun) {
    output.info?.("No active Villani run to abort.");
    return;
  }
  output.warn?.(activeRun.phase === "starting" ? "Aborting Villani run during startup…" : "Aborting active Villani run…");
  await abortActiveRun("Aborted by /villani-abort");
}

async function abortActiveRun(_reason: string): Promise<void> {
  const run = activeRun;
  if (!run) return;
  run.phase = "aborting";
  run.abortController.abort();
  try {
    run.bridge?.abort(run.id);
  } catch {
    run.bridge?.kill();
  }
  await Promise.race([
    run.done,
    new Promise<void>((resolve) => setTimeout(resolve, 5_000)).then(() => run.bridge?.kill()),
  ]);
}

async function resolveModelAuth(ctx: ExtensionCommandContext, model: Model<string>): Promise<{ apiKey?: string; headers?: Record<string, string> }> {
  const auth = await ctx.modelRegistry.getApiKeyAndHeaders(model);
  if (!auth.ok) throw new Error(`Villani could not resolve Pi model authentication: ${sanitizeErrorMessage(auth.error)}`);
  return { apiKey: auth.apiKey, headers: auth.headers };
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

function useExplicitVillaniConfig(): boolean {
  return String(process.env.VILLANI_USE_PI_MODEL ?? "").toLowerCase() === "false";
}

function uiOutput(ui: ExtensionUIContext): PiLikeOutput {
  return {
    info: (message: string) => ui.notify(message, "info"),
    warn: (message: string) => ui.notify(message, "warning"),
    error: (message: string) => ui.notify(message, "error"),
    markdown: (message: string) => ui.notify(message, "info"),
  };
}

function sanitizeErrorMessage(message: string): string {
  return message
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [redacted]")
    .replace(/api[_-]?key[=:]\s*[^\s,;]+/gi, "api_key=[redacted]")
    .slice(0, 500);
}

export function __getActiveRunForTests(): ActiveVillaniRun | undefined {
  return activeRun;
}
