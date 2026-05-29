import { randomUUID } from "node:crypto";
import { BridgeEvent, RunCommand, VillaniMode } from "./protocol";
import { VillaniBridgeProcess } from "./process";
import { PiLikeOutput, renderEvent } from "./render";

interface PiLikeContext {
  commands?: { registerCommand?: (name: string, handler: (args?: unknown) => unknown) => { dispose?: () => void } | void };
  workspace?: { cwd?: string; rootPath?: string; folders?: Array<{ path?: string; uri?: { fsPath?: string } }> };
  output?: PiLikeOutput;
  subscriptions?: Array<{ dispose?: () => void }>;
}

export function activate(context: PiLikeContext): void {
  const registrar = context.commands?.registerCommand;
  if (!registrar) {
    throw new Error("Pi command registration API was not found; update src/index.ts for the installed Pi SDK.");
  }
  const disposable = registrar("villani", async (args?: unknown) => runVillaniCommand(context, args));
  if (disposable && context.subscriptions) context.subscriptions.push(disposable);
}

export function deactivate(): void {
  // Nothing persistent to clean up; per-run bridge processes are killed in finally blocks.
}

export async function runVillaniCommand(context: PiLikeContext, args?: unknown): Promise<void> {
  const task = extractTask(args);
  const output = context.output ?? console;
  if (!task) throw new Error("Usage: /villani <task>");
  const repo = resolveWorkspace(context);
  const runId = randomUUID();
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
    await bridge.waitUntilReady();
    const command: RunCommand = {
      type: "run",
      id: runId,
      task,
      repo,
      mode: (process.env.VILLANI_MODE as VillaniMode | undefined) || "runner",
      config: {
        provider: process.env.VILLANI_PROVIDER,
        model: process.env.VILLANI_MODEL,
        base_url: process.env.VILLANI_BASE_URL,
        api_key: process.env.VILLANI_API_KEY,
      },
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
  }
}

function extractTask(args: unknown): string {
  if (typeof args === "string") return args.trim();
  if (args && typeof args === "object" && "text" in args) return String((args as { text?: unknown }).text ?? "").trim();
  if (Array.isArray(args)) return args.join(" ").trim();
  return "";
}

function resolveWorkspace(context: PiLikeContext): string {
  const workspace = context.workspace;
  return workspace?.cwd || workspace?.rootPath || workspace?.folders?.[0]?.path || workspace?.folders?.[0]?.uri?.fsPath || process.cwd();
}

