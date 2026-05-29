import { randomUUID } from "node:crypto";
import type { ExtensionAPI, ExtensionCommandContext, ExtensionUIContext } from "@earendil-works/pi-coding-agent";
import type { Model } from "@earendil-works/pi-ai";
import { BridgeEvent, RunCommand, VillaniMode } from "./protocol.js";
import { startVillaniBridgeProcess, VillaniBridgeProcess } from "./process.js";
import { PiModelProxy } from "./modelProxy.js";
import { PiLikeOutput, renderEvent } from "./render.js";

interface ActiveVillaniRun {
  id: string;
  repo: string;
  bridge: VillaniBridgeProcess;
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
  const explicitConfig = useExplicitVillaniConfig();
  const model = ctx.model as Model<string> | undefined;
  if (!explicitConfig && !model) {
    output.error?.("No active Pi model is available for Villani. Select/configure a Pi model, or set VILLANI_USE_PI_MODEL=false with VILLANI_PROVIDER, VILLANI_MODEL, and VILLANI_BASE_URL.");
    return;
  }

  const proxy = !explicitConfig && model ? new PiModelProxy({ model, signal: ctx.signal }) : undefined;
  let bridge: VillaniBridgeProcess | undefined;
  let finished = false;
  let resolveFinal!: () => void;
  const finalEvent = new Promise<void>((resolve) => { resolveFinal = resolve; });
  let resolveDone!: () => void;
  const done = new Promise<void>((resolve) => { resolveDone = resolve; });

  try {
    const proxyUrl = proxy ? await proxy.start() : undefined;
    bridge = await startVillaniBridgeProcess({ command: process.env.VILLANI_COMMAND, cwd: repo });
    activeRun = { id: runId, repo, bridge, proxy, done, resolveDone };
    bridge.onEvent((event: BridgeEvent) => {
      renderEvent(event, output);
      if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_aborted") {
        finished = true;
        resolveFinal();
      }
      if (event.type === "error") {
        output.error?.(`Villani bridge error: ${event.error}`);
      }
    });
    ctx.signal?.addEventListener("abort", () => {
      void abortActiveRun("Pi command cancellation requested");
    }, { once: true });

    const command: RunCommand = {
      type: "run",
      id: runId,
      task,
      repo,
      mode: (process.env.VILLANI_MODE as VillaniMode | undefined) || "runner",
      config: buildRunConfig(proxyUrl, model),
    };
    bridge.send(command);
    await Promise.race([
      finalEvent,
      bridge.waitForExit().then((code) => {
        if (!finished) throw new Error(`Villani bridge exited before a final event with code ${code}. ${bridge?.stderr() ?? ""}`.trim());
      }),
    ]);
  } catch (error) {
    output.error?.(error instanceof Error ? error.message : String(error));
  } finally {
    if (!finished && bridge) {
      try { bridge.abort(runId); } catch { /* ignore cleanup races */ }
    }
    bridge?.kill();
    await proxy?.stop();
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
  output.warn?.("Aborting active Villani run…");
  await abortActiveRun("Aborted by /villani-abort");
}

async function abortActiveRun(_reason: string): Promise<void> {
  const run = activeRun;
  if (!run) return;
  try {
    run.bridge.abort(run.id);
  } catch {
    run.bridge.kill();
  }
  await Promise.race([
    run.done,
    new Promise<void>((resolve) => setTimeout(resolve, 5_000)).then(() => run.bridge.kill()),
  ]);
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
  if (String(process.env.VILLANI_USE_PI_MODEL ?? "").toLowerCase() === "false") return true;
  return false;
}

function uiOutput(ui: ExtensionUIContext): PiLikeOutput {
  return {
    info: (message: string) => ui.notify(message, "info"),
    warn: (message: string) => ui.notify(message, "warning"),
    error: (message: string) => ui.notify(message, "error"),
    markdown: (message: string) => ui.notify(message, "info"),
  };
}

export function __getActiveRunForTests(): ActiveVillaniRun | undefined {
  return activeRun;
}
