import { Text } from "@earendil-works/pi-tui";

export type VillaniUiState = {
  phase: string;
  lastCommand?: string;
  lastCommandExitCode?: number;
  lastCommandPreview?: string;
  lastAssistantText?: string;
  finalSummary?: string;
  changedFiles?: string[];
  transcriptPath?: string;
  lastEventAt?: number;
};

const USER_FACING_EVENT_TYPES = new Set([
  "stream_text",
  "tool_started",
  "command_started",
  "command_finished",
  "approval_required",
  "approval_resolved",
  "run_completed",
  "run_failed",
  "run_aborted",
  "model_request_started",
  "model_request_completed",
  "model_request_failed",
  "proxy_request_started",
  "proxy_request_completed",
  "proxy_request_failed",
]);

export function shouldRenderUserFacingEvent(event: any): boolean {
  if (!event || typeof event !== "object") return false;
  const type = String(event.type || "");
  if (!USER_FACING_EVENT_TYPES.has(type)) return false;
  if (
    type === "bridge_diagnostic" ||
    type === "runner_heartbeat" ||
    type === "pong"
  )
    return false;
  const text = `${event.message || ""} ${event.text || ""}`;
  if (/bridge heartbeat pong|event received|tool_result mapped/i.test(text))
    return false;
  return true;
}

export function cleanAssistantText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.replace(/\r\n/g, "\n").trim();
  if (!text) return null;
  return text;
}

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
    parts.push(`stderr:\n${String(event.stderr_preview).slice(0, 500)}`);
  if (!event.stderr_preview && event.stdout_preview)
    parts.push(`output:\n${String(event.stdout_preview).slice(0, 500)}`);
  return parts.join("\n");
}
function toolStartedMessage(event: any): string {
  const tool = String(event.tool || event.name || "tool");
  const input = event.input && typeof event.input === "object" ? event.input : {};
  const path = event.path || input.path || input.file_path || event.file_path;
  if (tool === "GitStatus") return "Checking repository status";
  if (tool === "GitDiff") return "Reading current changes";
  if (tool === "GitLog") return "Reading git history";
  if (tool === "Read") return `Reading file: ${path || "unknown"}`;
  if (tool === "Write") return `Writing file: ${path || "unknown"}`;
  if (tool === "Patch") return `Applying patch: ${path || "unknown"}`;
  if (tool === "Bash") return "Preparing command";
  return "Villani is working...";
}
export function reduceVillaniUiState(
  state: VillaniUiState,
  event: any,
): VillaniUiState {
  const next = { ...state, lastEventAt: Date.now() };
  if (event.type === "run_started") next.phase = "Starting Villani...";
  else if (event.type === "model_request_started") {
    next.phase = "Villani is thinking...";
    next.lastCommand = undefined;
    next.lastCommandExitCode = undefined;
    next.lastCommandPreview = undefined;
  } else if (event.type === "proxy_request_started")
    next.phase = "Villani is thinking...";
  else if (event.type === "model_request_completed")
    next.phase = "Villani is thinking...";
  else if (event.type === "proxy_request_completed")
    next.phase = "Villani is thinking...";
  else if (event.type === "approval_required") next.phase = "Waiting for approval...";
  else if (event.type === "approval_resolved") next.phase = "Villani is thinking...";
  else if (event.type === "tool_started") {
    const tool = String(event.tool || event.name || "");
    next.phase =
      tool === "Read" || tool.startsWith("Git")
        ? "Reading files..."
        : tool === "Patch" || tool === "Write"
          ? "Applying changes..."
          : tool === "Bash"
            ? "Running command..."
            : "Villani is thinking...";
  }
  else if (event.type === "command_started") {
    next.phase = "Running command...";
    next.lastCommand = String(event.command || "").slice(0, 500);
    next.lastCommandExitCode = undefined;
    next.lastCommandPreview = undefined;
  } else if (event.type === "command_finished") {
    next.phase = "Finished command";
    next.lastCommand = String(event.command || next.lastCommand || "").slice(
      0,
      500,
    );
    next.lastCommandExitCode = event.exit_code;
    next.lastCommandPreview = preview(event);
  } else if (event.type === "verification_started")
    next.phase = "Villani is thinking...";
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
  if (state.phase === "Running command...")
    return ["Running command:", state.lastCommand || ""];
  if (state.phase === "Finished command")
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
export async function renderBridgeEvent(
  event: any,
  _pi: any,
  ctx: any,
): Promise<void> {
  const debug = process.env.VILLANI_PI_DEBUG === "1";
  if (!shouldRenderUserFacingEvent(event)) {
    if (
      debug &&
      event?.type === "bridge_diagnostic" &&
      !/heartbeat pong/i.test(String(event.message || ""))
    )
      console.error(
        `[pi-villani bridge] ${event.message || event.error || "diagnostic"}`,
      );
    return;
  }
  if (event.type === "approval_required") {
    state = reduceVillaniUiState(state, event);
    await setStatus(ctx, state.phase);
    await setWidget(ctx, ["Pending approval", event.summary || event.message || ""]);
    return;
  }
  if (event.type === "approval_resolved") {
    state = reduceVillaniUiState(state, event);
    await setStatus(ctx, state.phase);
    await setWidget(ctx, undefined);
    return;
  }
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
  ) {
    state = reduceVillaniUiState(state, event);
    await renderState(ctx, state);
    return;
  }
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
  if (event.type === "stream_text") {
    const text = cleanAssistantText(event.text ?? event.content);
    if (text && text !== state.lastAssistantText) {
      state = { ...state, lastAssistantText: text, lastEventAt: Date.now() };
      await notify(ctx, text, "info");
    }
    return;
  }
  if (event.type === "tool_started") {
    state = reduceVillaniUiState(state, event);
    await setStatus(ctx, state.phase);
    await notify(ctx, toolStartedMessage(event), "info");
    await setWidget(ctx, widgetForState(state));
    return;
  }
  if (event.type === "command_started") {
    state = reduceVillaniUiState(state, event);
    await setStatus(ctx, state.phase);
    await notify(ctx, `Running command:\n${state.lastCommand || ""}`, "info");
    await setWidget(ctx, widgetForState(state));
    return;
  }
  if (event.type === "command_finished") {
    state = reduceVillaniUiState(state, event);
    const msg = [
      `Command finished: exit ${state.lastCommandExitCode ?? "unknown"}`,
      state.lastCommandPreview || "",
    ]
      .filter(Boolean)
      .join("\n\n");
    await setStatus(ctx, state.phase);
    await notify(ctx, msg, "info");
    await setWidget(ctx, widgetForState(state));
    return;
  }
  state = reduceVillaniUiState(state, event);
  await renderState(ctx, state);
}
export function renderVillaniResultMessage(
  message: any,
  _options?: any,
  theme?: any,
): any {
  const text = extractVillaniResultText(message);

  let rendered = text;
  if (theme?.fg && message?.customType) {
    rendered = `${theme.fg("accent", "[villani-result]")}\n\n${text}`;
  }

  return new Text(rendered, 0, 0);
}

function extractVillaniResultText(message: any): string {
  const content = message?.content;

  if (typeof content === "string") return content;

  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        if (part && typeof part.text === "string") return part.text;
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }

  if (content && typeof content.text === "string") return content.text;

  return String(content ?? "");
}
export function resetVillaniUiState() {
  state = { phase: "Starting Villani..." };
}
