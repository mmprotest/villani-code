import { randomUUID } from "node:crypto";
import type { ExtensionAPI, ExtensionCommandContext, ExtensionUIContext } from "@earendil-works/pi-coding-agent";
import type { Model } from "@earendil-works/pi-ai";
import { BridgeEvent, RunCommand, VillaniMode } from "./protocol.js";
import { startVillaniBridgeProcess, VillaniBridgeProcess } from "./process.js";
import { resolveVillaniExecutable } from "./runtime.js";
import { PiModelProxy } from "./modelProxy.js";
import { PiLikeOutput, renderEvent } from "./render.js";

type ActiveRunPhase = "starting" | "running" | "aborting" | "completed";

const VILLANI_UI_KEY = "villani";

interface ActiveVillaniRun {
  id: string;
  repo: string;
  phase: ActiveRunPhase;
  abortController: AbortController;
  bridge?: VillaniBridgeProcess;
  proxy?: PiModelProxy;
  pendingApprovals: Set<string>;
  done: Promise<void>;
  resolveDone: () => void;
}

export type ApprovalPrompter = (request: Extract<BridgeEvent, { type: "approval_required" }>, ctx: ExtensionCommandContext, signal: AbortSignal) => Promise<boolean>;

type BridgeStarter = typeof startVillaniBridgeProcess;

let approvalPrompter: ApprovalPrompter = askUserForApproval;
let bridgeStarter: BridgeStarter = startVillaniBridgeProcess;

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
  const run: ActiveVillaniRun = { id: runId, repo, phase: "starting", abortController, pendingApprovals: new Set(), done, resolveDone };
  activeRun = run;
  setVillaniStatus(ctx.ui, "Villani: starting");

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

    setVillaniStatus(ctx.ui, "Villani: starting model proxy");
    run.proxy = !explicitConfig && model ? new PiModelProxy({ model, apiKey: auth?.apiKey, headers: auth?.headers, signal: abortController.signal }) : undefined;
    const proxyUrl = run.proxy ? await run.proxy.start() : undefined;
    if (abortController.signal.aborted) throw new Error("Villani run cancelled during startup.");

    setVillaniStatus(ctx.ui, "Villani: starting runtime");
    const executable = await resolveVillaniExecutable({
      overrideCommand: process.env.VILLANI_COMMAND,
      signal: abortController.signal,
      onProgress: (message) => output.info?.(message),
    });
    if (abortController.signal.aborted) throw new Error("Villani run cancelled during runtime setup.");
    reportRuntimeSource(executable, output);

    run.bridge = await bridgeStarter({ command: executable.executable, cwd: repo, signal: abortController.signal });
    run.phase = "running";
    setVillaniStatus(ctx.ui, "Villani: running");
    run.bridge.onEvent((event: BridgeEvent) => {
      if (abortController.signal.aborted && event.type === "run_completed") return;
      if (event.type === "approval_required") {
        void handleApprovalRequired(run, event, ctx, output).catch((error: unknown) => {
          safeWarn(output, `Approval handler failed unexpectedly; denied ${event.tool} request. ${formatUnknownError(error)}`);
          denyApprovalIfPending(run, event, output);
        });
        return;
      }
      renderEvent(event, output);
      if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_aborted") {
        finished = event.type === "run_completed";
        showFinalRunMessage(ctx.ui, event);
        clearVillaniUi(ctx.ui);
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
    if (abortController.signal.aborted) {
      output.warn?.(!run.bridge ? "Villani run cancelled during startup." : "Villani run cancelled.");
      showDurableMessage(ctx.ui, !run.bridge ? "Villani run cancelled during startup." : "Villani run cancelled.");
    } else {
      const message = error instanceof Error ? error.message : String(error);
      output.error?.(message);
      showDurableMessage(ctx.ui, `Villani failed: ${message}`);
    }
  } finally {
    clearVillaniUi(ctx.ui);
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
  setVillaniStatus(ctx.ui, "Villani: aborting");
  await abortActiveRun("Aborted by /villani-abort");
}

async function abortActiveRun(_reason: string): Promise<void> {
  const run = activeRun;
  if (!run) return;
  run.phase = "aborting";
  run.abortController.abort();
  for (const requestId of Array.from(run.pendingApprovals)) {
    try { run.bridge?.respondToApproval(run.id, requestId, false); } catch { /* bridge may already be closed */ }
    run.pendingApprovals.delete(requestId);
  }
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

async function handleApprovalRequired(
  run: ActiveVillaniRun,
  event: Extract<BridgeEvent, { type: "approval_required" }>,
  ctx: ExtensionCommandContext,
  output: PiLikeOutput,
): Promise<void> {
  run.pendingApprovals.add(event.request_id);
  let approved = false;

  try {
    setVillaniStatus(ctx.ui, `Villani: awaiting approval for ${event.tool}`);
    setVillaniWidget(ctx.ui, ["Villani is awaiting approval", event.summary]);
    approved = !run.abortController.signal.aborted && await approvalPrompter(event, ctx, run.abortController.signal);
  } catch (error) {
    approved = false;
    safeWarn(output, `Approval UI failed; denied ${event.tool} request. ${formatUnknownError(error)}`);
  }

  if (run.abortController.signal.aborted || activeRun?.id !== run.id) approved = false;
  if (!sendApprovalResponseIfPending(run, event, approved, output)) return;

  if (approved) {
    safeSetVillaniStatus(ctx.ui, "Villani: running", output);
    safeSetVillaniWidget(ctx.ui, undefined, output);
  } else {
    safeSetVillaniStatus(ctx.ui, `Villani: denied ${event.tool} approval`, output);
    safeSetVillaniWidget(ctx.ui, ["Villani approval denied", event.summary], output);
  }
  safeWarn(output, `${approved ? "Approved" : "Denied"} Villani ${event.tool} request: ${event.summary}`);
}

function denyApprovalIfPending(
  run: ActiveVillaniRun,
  event: Extract<BridgeEvent, { type: "approval_required" }>,
  output: PiLikeOutput,
): void {
  sendApprovalResponseIfPending(run, event, false, output);
}

function sendApprovalResponseIfPending(
  run: ActiveVillaniRun,
  event: Extract<BridgeEvent, { type: "approval_required" }>,
  approved: boolean,
  output: PiLikeOutput,
): boolean {
  const stillPending = run.pendingApprovals.delete(event.request_id);
  if (!stillPending) return false;
  try {
    run.bridge?.respondToApproval(run.id, event.request_id, approved);
  } catch {
    if (!run.abortController.signal.aborted) safeWarn(output, `Could not send approval response for ${event.tool}; bridge is no longer available.`);
  }
  return true;
}

function safeSetVillaniStatus(ui: ExtensionUIContext, text: string | undefined, output: PiLikeOutput): void {
  try {
    setVillaniStatus(ui, text);
  } catch (error) {
    safeWarn(output, `Could not update Villani status UI. ${formatUnknownError(error)}`);
  }
}

function safeSetVillaniWidget(ui: ExtensionUIContext, lines: string[] | undefined, output: PiLikeOutput): void {
  try {
    setVillaniWidget(ui, lines);
  } catch (error) {
    safeWarn(output, `Could not update Villani widget UI. ${formatUnknownError(error)}`);
  }
}

function safeWarn(output: PiLikeOutput, message: string): void {
  try {
    output.warn?.(message);
  } catch {
    // Ignore notification failures so approval responses are never blocked by warning UI.
  }
}

function safeInfo(output: PiLikeOutput, message: string): void {
  try {
    output.info?.(message);
  } catch {
    // Ignore diagnostics failures; they must not affect runtime startup.
  }
}

function formatUnknownError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (error === undefined || error === null) return "";
  return String(error);
}

function reportRuntimeSource(executable: Awaited<ReturnType<typeof resolveVillaniExecutable>>, output: PiLikeOutput): void {
  const version = executable.version ? ` v${executable.version}` : "";
  if (executable.source === "override") {
    safeInfo(output, `Villani runtime: using VILLANI_COMMAND override (${executable.executable}).`);
    return;
  }
  safeInfo(output, `Villani runtime: using ${executable.source}${version} at ${executable.executable}.`);
}

function setVillaniStatus(ui: ExtensionUIContext, text: string | undefined): void {
  const setter = ui.setStatus as ((key: string, text?: string) => void) | undefined;
  setter?.(VILLANI_UI_KEY, text);
}

function setVillaniWidget(ui: ExtensionUIContext, lines: string[] | undefined): void {
  const setter = ui.setWidget as ((key: string, lines?: string[], options?: { placement?: "aboveEditor" }) => void) | undefined;
  setter?.(VILLANI_UI_KEY, lines, { placement: "aboveEditor" });
}

function clearVillaniWidget(ui: ExtensionUIContext): void {
  setVillaniWidget(ui, undefined);
}

function clearVillaniUi(ui: ExtensionUIContext): void {
  setVillaniStatus(ui, undefined);
  clearVillaniWidget(ui);
}

function showDurableMessage(ui: ExtensionUIContext, message: string): void {
  ui.notify?.(message);
}

function showFinalRunMessage(ui: ExtensionUIContext, event: Extract<BridgeEvent, { type: "run_completed" | "run_failed" | "run_aborted" }>): void {
  if (event.type === "run_completed") {
    showDurableMessage(ui, event.summary || "Villani completed.");
    return;
  }
  if (event.type === "run_aborted") {
    showDurableMessage(ui, event.summary || "Villani aborted.");
    return;
  }
  showDurableMessage(ui, event.summary || event.error || "Villani failed.");
}

async function askUserForApproval(request: Extract<BridgeEvent, { type: "approval_required" }>, ctx: ExtensionCommandContext, signal: AbortSignal): Promise<boolean> {
  if (!ctx.hasUI || typeof ctx.ui.confirm !== "function") return false;
  return ctx.ui.confirm(approvalTitle(request), approvalMessage(request), { signal });
}

function approvalTitle(request: Extract<BridgeEvent, { type: "approval_required" }>): string {
  if (request.tool === "Write") return "Villani wants to write a file";
  if (request.tool === "Patch") return "Villani wants to apply a patch";
  if (request.tool === "Bash") return "Villani wants to run a shell command";
  return `Villani wants approval for ${request.tool}`;
}

function approvalMessage(request: Extract<BridgeEvent, { type: "approval_required" }>): string {
  const path = typeof request.input.path === "string" ? request.input.path : undefined;
  const command = typeof request.input.command === "string" ? request.input.command : undefined;
  const lines = [request.summary, ""];
  if (path) lines.push(`File: ${path}`, "");
  if (command) lines.push("Command:", command, "");
  lines.push("Allow this operation?");
  return lines.join("\n");
}

export function __setApprovalPrompterForTests(prompter: ApprovalPrompter): () => void {
  const previous = approvalPrompter;
  approvalPrompter = prompter;
  return () => { approvalPrompter = previous; };
}

export function __setBridgeStarterForTests(starter: BridgeStarter): () => void {
  const previous = bridgeStarter;
  bridgeStarter = starter;
  return () => { bridgeStarter = previous; };
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
