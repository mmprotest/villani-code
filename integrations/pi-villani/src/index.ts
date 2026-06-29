import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { resolveVillaniRuntime, cacheRoot } from "./runtime.js";
import {
  VILLANI_RUNTIME_TAG,
  VILLANI_RUNTIME_VERSION,
} from "./runtimeConfig.js";
import { VillaniBridgeProcess } from "./process.js";
import {
  resolvePiModel,
  sanitizeError,
  startModelProxyFromPiModel,
} from "./modelProxy.js";
import {
  confirm,
  finalMessage,
  nextVillaniStatus,
  notify,
  renderBridgeEvent,
  resetVillaniUiState,
  sendDurableVillaniMessage,
  setStatus,
  setWidget,
} from "./render.js";

type ActiveRun = {
  id: string;
  abort: AbortController;
  bridge?: VillaniBridgeProcess;
  proxy?: { close: () => void | Promise<void> };
  pending: Map<string, boolean>;
};
let activeRun: ActiveRun | null = null;
export function buildChildEnvForProxy(
  proxyUrl: string,
  model: any,
): NodeJS.ProcessEnv {
  const env = { ...process.env };
  for (const k of Object.keys(env)) {
    if (
      ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "VILLANI_API_KEY"].includes(k) ||
      /(_API_KEY|_TOKEN|_AUTH|_BEARER|TOKEN|AUTH|BEARER)$/i.test(k)
    )
      delete env[k];
  }
  env.VILLANI_PROVIDER = "openai";
  env.VILLANI_MODEL = model?.id ?? "pi-current-model";
  env.VILLANI_BASE_URL = proxyUrl;
  return env;
}
function envConfig() {
  return {
    provider: process.env.VILLANI_PROVIDER,
    model: process.env.VILLANI_MODEL,
    base_url: process.env.VILLANI_BASE_URL,
    api_key: process.env.VILLANI_API_KEY,
  };
}
export async function resolveModelAuth(ctx: any, model: any) {
  if (!ctx.modelRegistry?.getApiKeyAndHeaders)
    throw new Error("Pi modelRegistry.getApiKeyAndHeaders is unavailable.");
  const auth = await ctx.modelRegistry.getApiKeyAndHeaders(model);
  if (!auth?.ok)
    throw new Error(
      `Villani could not resolve Pi model authentication: ${sanitizeError(auth?.error ?? "unknown error")}`,
    );
  return { apiKey: auth.apiKey, headers: auth.headers };
}
export async function safeCommand(
  ctx: any,
  label: string,
  fn: () => Promise<void>,
): Promise<void> {
  try {
    await fn();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await notify(ctx, `${label} failed: ${message}`, "error");
    if (process.env.VILLANI_PI_DEBUG === "1") console.error(error);
  }
}
export function approvalTitle(request: any) {
  const tool = String(request.tool || "");
  if (tool === "Bash") return "Villani requests command authority";
  if (tool === "Read") return "Villani requests dossier access";
  if (tool === "Write") return "Villani requests edit authority";
  if (tool === "Patch") return "Villani requests patch authority";
  if (tool === "GitStatus") return "Villani requests repository inspection";
  if (tool === "GitDiff") return "Villani requests diff inspection";
  return "Villani requests approval";
}
export function approvalMessage(request: any) {
  const input =
    request.input &&
    typeof request.input === "object" &&
    !Array.isArray(request.input)
      ? request.input
      : {};

  const tool = String(request.tool || "operation");
  const command = typeof input.command === "string" ? input.command : undefined;
  const path =
    typeof input.path === "string" ? input.path :
    typeof input.file_path === "string" ? input.file_path :
    typeof request.path === "string" ? request.path :
    undefined;

  const lines: string[] = [];

  if (tool === "Bash" && command) lines.push(`Command: ${command}`, "");
  else if ((tool === "Write" || tool === "Patch") && path) lines.push(`File: ${path}`, "");
  else {
    lines.push("Operation:", tool, "");
    if (command) lines.push(`Command: ${command}`, "");
    if (path) lines.push(`File: ${path}`, "");
  }

  lines.push("Approve this Villani action?");
  return lines.join("\n");
}
async function handleApproval(run: ActiveRun, ctx: any, e: any) {
  const requestId = e.request_id || e.requestId;
  if (!requestId || run.pending.has(requestId)) return;
  const tool = String(e.tool || "tool");
  run.pending.set(requestId, false);
  let approved = false;
  const message = approvalMessage(e);
  try {
    await setWidget(ctx, undefined);
    await setStatus(ctx, nextVillaniStatus("approval", requestId) ?? "Villaniclearance required...");
    approved = await confirm(ctx, approvalTitle(e), message, {
      signal: run.abort.signal,
    });
  } catch (err) {
    approved = false;
    await notify(
      ctx,
      `Villani approval UI failed; denying request: ${err instanceof Error ? err.message : String(err)}`,
      "warn",
    );
  } finally {
    await setWidget(ctx, undefined);
  }
  if (run.pending.get(requestId) !== false) return;
  run.pending.set(requestId, true);
  await setStatus(ctx, approved ? "Villani resumes operation..." : "Villani records denial...");
  run.bridge?.respondToApproval(run.id, requestId, approved);
  if (process.env.VILLANI_PI_DEBUG === "1")
    console.error("[pi-villani bridge] approval response sent");
}
function denyPending(run: ActiveRun) {
  for (const [id, done] of run.pending) {
    if (!done) {
      run.pending.set(id, true);
      run.bridge?.respondToApproval(run.id, id, false);
    }
  }
}
function bridgeStderr(bridge: VillaniBridgeProcess) {
  return bridge.getRecentStderr?.() ?? bridge.stderr ?? "";
}
function bridgeDiagnosticMessage(e: unknown) {
  const msg = sanitizeError(e);
  if (/ModuleNotFoundError: No module named ['"]villani_code['"]/.test(msg))
    return `Villani bridge failed because the selected VILLANI_COMMAND could not import villani_code.
From repo root, run:
.\\.venv\\Scripts\\python.exe -m pip install -e .

${msg}`;
  return msg;
}
async function assertBridgePing(
  executable: string,
  ctx: any,
  env?: NodeJS.ProcessEnv,
  signal?: AbortSignal,
) {
  const bridge = new VillaniBridgeProcess(executable, {
    env,
    startupTimeoutMs: 30000,
    cwd: ctx.cwd ?? process.cwd(),
  });
  try {
    await bridge.waitUntilReady(undefined, signal);
    bridge.send({ type: "ping" });
    const pong = await bridge.waitForEvent("pong", 5000, signal);
    if (!pong)
      throw new Error(`Villani bridge ping timed out. ${bridgeStderr(bridge)}`);
    await notify(ctx, "Villani bridge ping succeeded.", "info");
    return true;
  } catch (e) {
    throw new Error(bridgeDiagnosticMessage(e));
  } finally {
    bridge.kill();
    await bridge.waitForExit(1500).catch(() => {});
  }
}
async function waitForRunAcknowledgement(
  bridge: VillaniBridgeProcess,
  runId: string,
  signal?: AbortSignal,
) {
  const event = await bridge.waitForAnyEvent(
    ["run_started", "error", "run_failed", "run_aborted"],
    runId,
    10000,
    signal,
  );
  if (event?.type === "run_started") return event;
  if (event) {
    const msg = event.error || event.message || event.summary || event.type;
    throw new Error(`Villani bridge rejected run command: ${msg}`);
  }
  throw new Error(
    `Villani bridge did not acknowledge run command within 10 seconds. ${bridgeStderr(bridge)}`,
  );
}
async function waitForFirstProgressAfterStart(
  bridge: VillaniBridgeProcess,
  runId: string,
  signal?: AbortSignal,
) {
  const event = await bridge.waitForAnyEvent(
    [
      "model_request_started",
      "approval_required",
      "tool_started",
      "phase",
      "run_completed",
      "run_failed",
      "run_aborted",
    ],
    runId,
    60000,
    signal,
  );
  if (!event)
    throw new Error(
      "Villani stalled after run_started before any model/tool/progress event.",
    );
  return event;
}
export async function runVillani(
  task: string,
  pi: any = {},
  ctx: any = pi,
): Promise<void> {
  resetVillaniUiState();
  await notify(ctx, "Villani starting...", "info");
  await setStatus(ctx, nextVillaniStatus("thinking", "startup") ?? "Villaniplan forming...");
  if (!task?.trim()) {
    await notify(ctx, "/villani requires a task argument", "warn");
    return;
  }
  if (activeRun) {
    await notify(
      ctx,
      "Villani is already running. Wait for the active run to finish or cancel the session.",
      "warn",
    );
    return;
  }
  const runId = randomUUID();
  const abort = new AbortController();
  const run: ActiveRun = { id: runId, abort, pending: new Map() };
  let postToolTimer: NodeJS.Timeout | undefined;
  let heartbeatTimer: NodeJS.Timeout | undefined;
  let heartbeatSeq = 0;
  const clearPostToolTimer = () => {
    if (postToolTimer) {
      clearTimeout(postToolTimer);
      postToolTimer = undefined;
    }
  };
  const clearHeartbeat = () => {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = undefined;
    }
  };
  activeRun = run;
  try {
    let model: any;
    let proxy: any;
    const usePi = process.env.VILLANI_USE_PI_MODEL !== "false";
    let config: any;
    let env: any;
    if (usePi) {
      await setStatus(ctx, nextVillaniStatus("analysis", "model-connection") ?? "Villanalysis begins...");
      model = await resolvePiModel(ctx);
      const auth = await resolveModelAuth(ctx, model);
      await setStatus(ctx, nextVillaniStatus("analysis", "model-connection") ?? "Villanalysis begins...");
      proxy = await startModelProxyFromPiModel({
        model,
        apiKey: auth.apiKey,
        headers: auth.headers,
        signal: abort.signal,
        timeoutMs: 300000,
        onEvent: (e: any) => void renderBridgeEvent(e, pi, ctx),
      });
      run.proxy = proxy;
      config = {
        provider: "openai",
        model: model.id ?? "pi-current-model",
        base_url: proxy.url,
        pi_model_proxy: true,
      };
      env = buildChildEnvForProxy(proxy.url, model);
    } else {
      model = { id: process.env.VILLANI_MODEL };
      config = envConfig();
      env = process.env;
    }
    await setStatus(ctx, nextVillaniStatus("thinking", "runtime-start") ?? "Villaniplan forming...");
    const executable = await resolveVillaniRuntime({ signal: abort.signal });
    if (process.env.VILLANI_COMMAND) {
      await setStatus(ctx, nextVillaniStatus("thinking", "runtime-start") ?? "Villaniplan forming...");
      await assertBridgePing(executable, ctx, env, abort.signal);
    }
    await setStatus(ctx, nextVillaniStatus("thinking", "runtime-start") ?? "Villaniplan forming...");
    const bridge = new VillaniBridgeProcess(executable, {
      env,
      startupTimeoutMs: 30000,
      proxyMode: usePi,
      explicitConfigMode: !usePi,
      cwd: ctx.cwd ?? process.cwd(),
    });
    run.bridge = bridge;
    await setStatus(ctx, nextVillaniStatus("thinking", "runtime-start") ?? "Villaniplan forming...");
    await bridge.waitUntilReady(undefined, abort.signal);
    await setStatus(ctx, nextVillaniStatus("thinking", "approval-resolved") ?? "Villanithoughts classified...");
    const startPostToolTimer = (eventType: string) => {
      clearPostToolTimer();
      postToolTimer = setTimeout(async () => {
        try {
          bridge.send({ type: "ping", id: `${runId}-post-tool-ping` });
          const pong = await bridge.waitForEvent("pong", 3000, abort.signal);
          if (pong) {
            const status = nextVillaniStatus("thinking", "post-tool-ping");
            if (status) await setStatus(ctx, status);
          }
          else
            await notify(
              ctx,
              `Villani bridge did not respond while waiting for next runner event. ${bridgeStderr(bridge)}`,
              "error",
            );
        } catch (err) {
          await notify(
            ctx,
            `Villani bridge ping failed while waiting: ${sanitizeError(err)} ${bridgeStderr(bridge)}`,
            "error",
          );
        }
      }, 5000);
      postToolTimer.unref?.();
    };
    const onBridgeEvent = (e: any) => {
      if (process.env.VILLANI_PI_DEBUG === "1" && e.type !== "pong")
        console.error(`[pi-villani bridge] event received: ${e.type}`);
      if (
        [
          "model_request_started",
          "model_request_completed",
          "proxy_request_started",
          "proxy_request_completed",
          "command_started",
          "command_finished",
          "tool_started",
          "approval_required",
          "stream_text",
          "run_completed",
          "run_failed",
          "run_aborted",
        ].includes(e.type)
      )
        clearPostToolTimer();
      if (e.type === "approval_required") void handleApproval(run, ctx, e);
      else void renderBridgeEvent(e, pi, ctx);
      if (e.type === "tool_result" || e.type === "tool_finished")
        startPostToolTimer(e.type);
    };
    bridge.off("event", onBridgeEvent);
    bridge.on("event", onBridgeEvent);
    heartbeatTimer = setInterval(async () => {
      try {
        const id = `${runId}-heartbeat-${++heartbeatSeq}`;
        bridge.send({ type: "ping", id });
        const pong = await bridge.waitForEvent("pong", 3000, abort.signal);
        if (!pong)
          await notify(
            ctx,
            `Villani bridge heartbeat failed. ${bridgeStderr(bridge)}`,
            "error",
          );
        else if (process.env.VILLANI_PI_DEBUG === "1")
          console.error("[pi-villani bridge] heartbeat pong");
      } catch (err) {
        await notify(
          ctx,
          `Villani bridge heartbeat failed: ${sanitizeError(err)} ${bridgeStderr(bridge)}`,
          "error",
        );
      }
    }, 15000);
    heartbeatTimer.unref?.();
    const runStartedPromise = waitForRunAcknowledgement(
      bridge,
      runId,
      abort.signal,
    );
    const progressPromise = waitForFirstProgressAfterStart(
      bridge,
      runId,
      abort.signal,
    );
    let finished = false;
    const finalPromise = bridge
      .waitForFinalEvent(runId, 30 * 60 * 1000, abort.signal)
      .then((event) => {
        finished = true;
        return event;
      });
    const exitPromise = bridge.waitForExit(0, abort.signal).then((code) => {
      if (!finished && !abort.signal.aborted) {
        throw new Error(
          `Villani bridge exited before final event with code ${code}. ${bridgeStderr(bridge)}`,
        );
      }
      return undefined;
    });
    bridge.send({
      type: "run",
      id: runId,
      task,
      repo: ctx.cwd ?? process.cwd(),
      mode: "runner",
      config,
    });
    await setStatus(ctx, nextVillaniStatus("thinking", "approval-resolved") ?? "Villanithoughts classified...");
    try {
      await runStartedPromise;
    } catch (e) {
      abort.abort();
      progressPromise.catch(() => {});
      finalPromise.catch(() => {});
      exitPromise.catch(() => {});
      bridge.kill();
      await proxy?.close?.();
      await bridge.waitForExit(1500).catch(() => {});
      throw e;
    }
    await Promise.race([progressPromise, finalPromise]);
    const final = await Promise.race([finalPromise, exitPromise]);
    if (final) {
      clearPostToolTimer();
      clearHeartbeat();
      await setWidget(ctx, undefined);
      await setStatus(
        ctx,
        final.type === "run_completed"
          ? "Completed"
          : final.type === "run_aborted"
            ? "Failed"
            : "Failed",
      );
      await sendDurableVillaniMessage(pi, ctx, finalMessage(final), final);
    }
  } finally {
    if (activeRun === run) {
      clearPostToolTimer();
      clearHeartbeat();
      denyPending(run);
      run.abort.abort();
      run.bridge?.kill();
      await run.proxy?.close?.();
      await run.bridge?.waitForExit(1500).catch(() => {});
      await setStatus(ctx, undefined);
      activeRun = null;
    }
  }
}
export async function proxyTest(ctx: any): Promise<void> {
  await notify(ctx, "Villani proxy test starting...", "info");
  const model = await resolvePiModel(ctx);
  await notify(ctx, `active model id: ${model?.id ?? "unavailable"}`, "info");
  await notify(
    ctx,
    `modelRegistry.getApiKeyAndHeaders exists: ${typeof ctx.modelRegistry?.getApiKeyAndHeaders === "function"}`,
    "info",
  );
  const auth = await resolveModelAuth(ctx, model);
  await notify(ctx, "auth resolution succeeded: true", "info");
  const proxy = await startModelProxyFromPiModel({
    model,
    apiKey: auth.apiKey,
    headers: auth.headers,
    timeoutMs: 300000,
    completeFn: ctx.completeFn,
    completeSource: ctx.completeSource,
    completeImporter: ctx.completeImporter,
    pi: ctx.pi,
  });
  try {
    await notify(ctx, `proxy URL: ${proxy.url}`, "info");
    await notify(
      ctx,
      "POST /v1/chat/completions reached proxy: sending",
      "info",
    );
    const r = await fetch(proxy.url + "/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: model.id ?? "pi-current-model",
        messages: [{ role: "user", content: "Say exactly READY" }],
        stream: false,
      }),
    });
    await notify(ctx, "POST /v1/chat/completions reached proxy: yes", "info");
    await notify(
      ctx,
      `selected Pi completion helper source: ${proxy.completionSource ?? "unavailable"}`,
      "info",
    );
    const text = await r.text();
    if (!r.ok) {
      await notify(
        ctx,
        `complete returned or failed: failed (${r.status})`,
        "info",
      );
      throw new Error(`HTTP ${r.status}: ${text}`);
    }
    await notify(ctx, "complete returned or failed: returned", "info");
    await notify(
      ctx,
      `Villani proxy test succeeded: ${text.slice(0, 500)}`,
      "info",
    );
  } finally {
    await proxy.close();
  }
}
export async function abortVillani(ctx: any = {}): Promise<boolean> {
  if (!activeRun) {
    await notify(ctx, "No active Villani run.", "info");
    return false;
  }
  const run = activeRun;
  await notify(ctx, "Aborting Villani...", "info");
  denyPending(run);
  run.bridge?.abort(run.id);
  const aborted = await run.bridge?.waitForEvent("run_aborted", 1500);
  run.abort.abort();
  if (!aborted) run.bridge?.kill();
  await run.proxy?.close?.();
  await run.bridge?.waitForExit(1500).catch(() => {});
  await setStatus(ctx, undefined);
  if (activeRun === run) activeRun = null;
  await notify(
    ctx,
    aborted ? "Villani aborted." : "Villani abort requested.",
    "info",
  );
  return true;
}
export async function bridgePing(ctx: any): Promise<void> {
  await notify(ctx, "Villani bridge ping starting...", "info");
  const executable = await resolveVillaniRuntime({});
  await notify(ctx, `runtime executable: ${executable}`, "info");
  await assertBridgePing(executable, ctx);
}
async function doctor(ctx: any) {
  const cached = (() => {
    try {
      return existsSync(cacheRoot());
    } catch {
      return false;
    }
  })();
  const lines = [
    `package version: 0.1.4`,
    `runtime version: ${VILLANI_RUNTIME_VERSION}`,
    `cwd: ${ctx.cwd ?? process.cwd()}`,
    `ctx.model exists: ${!!ctx.model}`,
    `ctx.model.id: ${ctx.model?.id ?? "unavailable"}`,
    `ctx.modelRegistry exists: ${!!ctx.modelRegistry}`,
    `ctx.modelRegistry.getApiKeyAndHeaders exists: ${typeof ctx.modelRegistry?.getApiKeyAndHeaders === "function"}`,
    `VILLANI_USE_PI_MODEL is set: ${process.env.VILLANI_USE_PI_MODEL !== undefined}`,
    `VILLANI_COMMAND is set: ${process.env.VILLANI_COMMAND !== undefined}`,
    `runtime tag: ${VILLANI_RUNTIME_TAG}`,
    `cached runtime executable exists: ${cached}`,
    `active run: ${activeRun ? "yes" : "no"}`,
  ];
  if (process.env.VILLANI_COMMAND) {
    try {
      const executable = await resolveVillaniRuntime({});
      await assertBridgePing(executable, ctx);
    } catch (e) {
      lines.push(
        `VILLANI_COMMAND bridge ping failed: ${bridgeDiagnosticMessage(e)}`,
      );
    }
  }
  await notify(ctx, lines.join("\n"), "info");
}
export default function activate(api: any) {
  const reg = (
    name: string,
    description: string,
    handler: (args: string, ctx: any) => Promise<void>,
  ) => api.registerCommand(name, { description, handler });
  reg("villani", "Run Villani Code on a task", async (args, ctx) =>
    safeCommand(ctx, "Villani", () => runVillani(args, ctx?.pi ?? api, ctx)),
  );
  try {
    api.onSessionStart?.(async (ctx: any) =>
      notify(ctx, "Villani extension loaded"),
    );
    api.on?.("sessionStart", async (ctx: any) =>
      notify(ctx, "Villani extension loaded"),
    );
  } catch {}
}
export { notify, setStatus, sendDurableVillaniMessage, confirm };
