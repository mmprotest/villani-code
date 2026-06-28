export type VillaniUiState = {
  phase: string;
  lastCommand?: string;
  lastCommandExitCode?: number;
  lastCommandPreview?: string;
  finalSummary?: string;
  changedFiles?: string[];
  transcriptPath?: string;
  lastEventAt?: number;
};

export async function notify(
  ctx: any,
  message: string,
  level: "info" | "warn" | "error" = "info",
): Promise<void> {
  try {
    if (ctx?.ui?.notify) await ctx.ui.notify(message, level);
    else (level === "error" ? console.error : console.log)(message);
  } catch {
    try {
      console.error(message);
    } catch {}
  }
}
export async function setStatus(
  ctx: any,
  message: string | undefined,
): Promise<void> {
  try {
    if (ctx?.ui?.setStatus) await ctx.ui.setStatus("villani", message);
  } catch {
    try {
      if (ctx?.ui?.setStatus) await ctx.ui.setStatus(message);
    } catch {}
  }
}
export async function setWidget(ctx: any, widget: any): Promise<void> {
  try {
    if (ctx?.ui?.setWidget) await ctx.ui.setWidget("villani", widget);
  } catch {}
}
export async function sendDurableVillaniMessage(
  pi: any,
  ctx: any,
  message: string,
  details?: any,
): Promise<void> {
  const payload = {
    customType: "villani-result",
    content: [{ type: "text", text: message }],
    display: true,
    details: sanitizeDetails(details),
  };
  try {
    if (typeof pi?.sendMessage === "function") {
      await pi.sendMessage(payload);
      return;
    }
  } catch {}
  await notify(ctx, message, "info");
}
export async function confirm(
  ctx: any,
  title: string,
  message: string,
  options?: any,
): Promise<boolean> {
  try {
    if (ctx?.ui?.confirm)
      return !!(await ctx.ui.confirm(title, message, options));
  } catch (e) {
    throw e;
  }
  return false;
}
export function visibleChangedFiles(files: string[] = []) {
  return files.filter(
    (f) => !/(^|\/)(\.villani|\.villani_code|__pycache__)(\/|$)|\.pyc$/.test(f),
  );
}
function sanitizeDetails(value: any, seen = new WeakSet<object>()): any {
  if (value === undefined || value === null) return value;
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  )
    return value;
  if (Array.isArray(value)) return value.map((v) => sanitizeDetails(v, seen));
  if (typeof value === "object") {
    if (seen.has(value)) return "[Circular]";
    seen.add(value);
    const out: any = {};
    for (const [key, entry] of Object.entries(value)) {
      if (
        /(api[_-]?key|authorization|auth|bearer|token|secret|cookie|headers?)$/i.test(
          key,
        ) ||
        /^(authorization|cookie)$/i.test(key)
      )
        continue;
      out[key] = sanitizeDetails(entry, seen);
    }
    seen.delete(value);
    return out;
  }
  return String(value);
}
function verificationStatus(event: any): string | undefined {
  const verification =
    event.verification_status ?? event.verificationStatus ?? event.verification;
  if (!verification) return undefined;
  if (typeof verification === "string") return `Verification: ${verification}`;
  if (typeof verification === "object") {
    const status =
      verification.status ?? verification.result ?? verification.outcome;
    return status ? `Verification: ${status}` : undefined;
  }
  return `Verification: ${String(verification)}`;
}
export function finalMessage(event: any) {
  const files = visibleChangedFiles(
    event.changed_files || event.changedFiles || [],
  );
  const head =
    event.type === "run_completed"
      ? "Villani completed"
      : event.type === "run_aborted"
        ? "Villani aborted"
        : "Villani failed";
  const transcript = event.transcript_path || event.transcriptPath;
  const verification = verificationStatus(event);
  const body =
    event.summary ||
    event.error ||
    event.message ||
    (event.type === "run_completed"
      ? "Villani completed. See transcript for details."
      : "No details were provided.");
  const last =
    event.type === "run_failed" && event.last_status
      ? `Last known status:\n${event.last_status}`
      : "";
  return [
    head,
    body,
    files.length
      ? `Changed files:\n${files.map((f: string) => `- ${f}`).join("\n")}`
      : "",
    last,
    transcript ? `Transcript: ${transcript}` : "",
    verification,
  ]
    .filter(Boolean)
    .join("\n\n");
}
function preview(event: any) {
  const parts = [];
  if (event.stderr_preview)
    parts.push(`stderr: ${String(event.stderr_preview).slice(0, 500)}`);
  if (event.stdout_preview)
    parts.push(`stdout: ${String(event.stdout_preview).slice(0, 500)}`);
  return parts.join("\n");
}
export function reduceVillaniUiState(
  state: VillaniUiState,
  event: any,
): VillaniUiState {
  const next = { ...state, lastEventAt: Date.now() };
  if (event.type === "run_started") next.phase = "Starting Villani...";
  else if (
    event.type === "model_request_started" ||
    event.type === "proxy_request_started"
  )
    next.phase = "Villani is thinking...";
  else if (
    event.type === "model_request_completed" ||
    event.type === "proxy_request_completed"
  )
    next.phase = "Checking result";
  else if (event.type === "approval_required")
    next.phase = "Villani wants approval";
  else if (event.type === "command_started") {
    next.phase = "Running command";
    next.lastCommand = String(event.command || "").slice(0, 500);
    next.lastCommandExitCode = undefined;
    next.lastCommandPreview = undefined;
  } else if (event.type === "command_finished") {
    next.phase = "Command finished";
    next.lastCommand = String(event.command || next.lastCommand || "").slice(
      0,
      500,
    );
    next.lastCommandExitCode = event.exit_code;
    next.lastCommandPreview = preview(event);
  } else if (event.type === "tool_result" || event.type === "tool_finished") {
    next.phase =
      event.tool === "Bash" ? "Command finished" : "Applying changes";
  } else if (event.type === "workspace_changed") {
    next.phase = "Applying changes";
    if (event.path)
      next.changedFiles = visibleChangedFiles([
        ...(next.changedFiles || []),
        String(event.path),
      ]);
  } else if (event.type === "verification_started")
    next.phase = "Checking result";
  else if (event.type === "run_completed") {
    next.phase = "Completed";
    next.finalSummary = event.summary;
    next.changedFiles = visibleChangedFiles(
      event.changed_files || event.changedFiles || next.changedFiles || [],
    );
    next.transcriptPath = event.transcript_path || event.transcriptPath;
  } else if (event.type === "run_failed") {
    next.phase = "Failed";
    next.finalSummary = event.summary || event.error;
  } else if (event.type === "run_aborted") next.phase = "Failed";
  else if (
    event.type === "runner_heartbeat" &&
    Date.now() - (state.lastEventAt || 0) > 15000
  )
    next.phase = "Villani is thinking...";
  return next;
}
export function widgetForState(state: VillaniUiState): any {
  if (state.phase === "Running command")
    return ["Running command:", state.lastCommand || ""];
  if (state.phase === "Command finished")
    return [
      `Command finished: exit ${state.lastCommandExitCode ?? "unknown"}`,
      state.lastCommandPreview || "",
    ]
      .filter(Boolean)
      .join("\n");
  if (state.phase === "Completed" || state.phase === "Failed") return undefined;
  if (state.changedFiles?.length)
    return `Changed files:\n${state.changedFiles.map((f) => `- ${f}`).join("\n")}`;
  return undefined;
}
export async function renderState(ctx: any, state: VillaniUiState) {
  await setStatus(ctx, state.phase);
  await setWidget(ctx, widgetForState(state));
}
let state: VillaniUiState = { phase: "Starting Villani..." };
let lastStreamAt = 0;
export async function renderBridgeEvent(
  event: any,
  _pi: any,
  ctx: any,
): Promise<void> {
  const debug = process.env.VILLANI_PI_DEBUG === "1";
  if (event.type === "approval_required") return;
  if (event.type === "bridge_diagnostic") {
    if (debug && !/heartbeat pong/i.test(String(event.message || "")))
      console.error(
        `[pi-villani bridge] ${event.message || event.error || "diagnostic"}`,
      );
    return;
  }
  if (event.type === "pong") return;
  if (
    event.type === "run_completed" ||
    event.type === "run_failed" ||
    event.type === "run_aborted"
  )
    return;
  if (event.type === "error") {
    await notify(
      ctx,
      `Villani error: ${event.error || event.message || "unknown error"}`,
      "error",
    );
    return;
  }
  if (
    event.type === "model_request_failed" ||
    event.type === "proxy_request_failed"
  ) {
    await setStatus(ctx, "Failed");
    await notify(
      ctx,
      `Villani model request failed: ${event.error || event.message || "unknown error"}`,
      "error",
    );
    return;
  }
  if (event.type === "tool_progress") {
    const msg = `Villani: ${String(event.message || "tool progress").slice(0, 500)}`;
    await setStatus(ctx, msg);
    return;
  }
  if (event.type === "stream_text") {
    const text = String(event.text || "")
      .trim()
      .slice(0, 240);
    const now = Date.now();
    if (text && now - lastStreamAt > 1000) {
      lastStreamAt = now;
      await setStatus(ctx, `Villani: ${text}`);
    }
    return;
  }
  state = reduceVillaniUiState(state, event);
  await renderState(ctx, state);
}
export function renderVillaniResultMessage(message: any): any {
  const text = Array.isArray(message?.content)
    ? message.content.map((p: any) => p?.text ?? "").join("\n")
    : String(message?.content ?? "");
  return text;
}
export function resetVillaniUiState() {
  state = { phase: "Starting Villani..." };
}
