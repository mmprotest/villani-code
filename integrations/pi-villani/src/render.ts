export type VillaniCopyCategory =
  | "thinking"
  | "analysis"
  | "reading"
  | "writing"
  | "running"
  | "testing"
  | "debugging"
  | "review"
  | "approval"
  | "failure"
  | "complete";

const VILLANI_COPY: Record<VillaniCopyCategory, string[]> = {
  thinking: ["Villani is make plan...", "Villaniplan forming...", "Villani thinks. Nobody interrupt.", "Villani has doctrine now...", "Villanithoughts classified...","Villani think. Room become unsafe.","Plan is weak. Villani improve.","Villani make brain do labour...","Villani is decide what reality means...","Villani has thought. Other thoughts dismissed...","Villani is make superior plan..."],
  analysis: ["Villanalysis begins...", "Villani inspect problem...", "Villani finds weak logic...", "Villanicommission investigates...", "Villani determines blame...","Villani is inspect problem...","Analysis now under state control...","Villani is find where truth escaped...","Villani is diagnose amateur logic...","Villani discovers obvious failure. Embarrassing."],
  reading: ["Villani reads file. File nervous.", "Villaniread begins...", "Villani open file for questioning...", "Villanidossier opened...", "Villani check file loyalty...","Villani is read file...","File contents now under suspicion...","File summoned for questioning...","Villani is see what file hide...","File classified. Villani has access...","Villaninterrogation begins...","Villani reads. File should be grateful.","Villani study file. File not impressive."],
  writing: ["Villani makes file obey...", "Villanipatch imposed...", "Villani writes new order...", "Villanification applied...", "Villani edits without remorse...","Villani patches. Code say thank you.","Villani is make file obey...","File corrected by authority...","Villani is fixing file attitude..."],
  running: ["Villani gives command...", "Villanicommand issued...", "Villani demands output...", "Villanirun begins...", "Villani expects obedience...","Machine must now explain itself...","Villani is launch operation..."],
  testing: ["Villani begins inspection...", "Villanitest begins...", "Villani demands green tests...", "Villaniverdict pending...", "Villani checks for lies...","Villanitest begins...","Villani is ask why fail...","Villaninspection. Code scared...","Code must prove usefulness...","Villani hunt fake success..."],
  debugging: ["Villani hunts weak bug...", "Villanidebug begins...", "Villani asks bug hard questions...", "Villanistack confesses...", "Villani removes instability...","Villani is hunt bug...","Villani correct disorder...","Villani is make error confess...","Bug hide. Villani interrogate..."],
  review: ["Villanireview begins...", "Villani judges patch...", "Villanicompliance checked...", "Villani approves, reluctantly...", "Villani check for betrayal...","Villani judge patch...","Villanicompliance checked...","Patch must justify existence..."],
  approval: [
    "Villani requires authorization...",
    "Villaniclearance required...",
    "Villani requests permission. Briefly.",
    "Villani pauses for approval...",
    "Villani awaits command authority...",
    "Villani demands signed order...",
    "Villanipermission pending...",
    "Villani asks council. Reluctantly.",
    "Villani is need approval...",
    "Villani want signed order...",
  ],
  failure: ["Villani sees failure. Unacceptable.", "Villanifailure recorded...", "Villani prepare punishment...", "Villani blames weak implementation...", "Villani demand second attempt...","Result is shameful. Villani informed...","Failure reported to Villani ministry...","Villani is record disgrace...","Villani blames weak architecture. Correctly."],
  complete: ["Villanified. Accept result.", "Villani declares victory...", "Villani restores order...", "Villanivictory logged...", "Villani permits ship...","Villani declare victory. Obviously.","Villani accept outcome as adequate tribute...","Villani create history. New world order established."],
};

const copyCounters = new Map<string, number>();
let lastStatusCategory: VillaniCopyCategory | undefined;
let lastStatusText: string | undefined;
let lastStatusAt = 0;
let lastStatusDetailKey: string | undefined;
let lastRenderedStatus: string | undefined;

export function villaniCopy(category: VillaniCopyCategory): string {
  const options = VILLANI_COPY[category];
  const current = copyCounters.get(category) ?? 0;
  copyCounters.set(category, current + 1);
  return options[current % options.length];
}

export function resetVillaniCopyCounters(): void {
  copyCounters.clear();
  resetVillaniStatusManager();
}

export function resetVillaniStatusManager(): void {
  lastStatusCategory = undefined;
  lastStatusText = undefined;
  lastStatusAt = 0;
  lastStatusDetailKey = undefined;
  lastRenderedStatus = undefined;
}

function shouldUpdateStatus(category: VillaniCopyCategory, detailKey?: string): boolean {
  const now = Date.now();
  if (category !== lastStatusCategory) return true;
  if (detailKey && detailKey !== lastStatusDetailKey) return true;
  if (now - lastStatusAt > 12000) return true;
  return false;
}

export function nextVillaniStatus(category: VillaniCopyCategory, detailKey?: string): string | undefined {
  if (!shouldUpdateStatus(category, detailKey)) return undefined;
  const text = villaniCopy(category);
  lastStatusCategory = category;
  lastStatusText = text;
  lastStatusAt = Date.now();
  lastStatusDetailKey = detailKey;
  return text;
}

export function currentStatusFallback(category: VillaniCopyCategory): string {
  return lastStatusCategory === category && lastStatusText ? lastStatusText : villaniCopy(category);
}

export type VillaniUiState = {
  phase: string;
  lastCommand?: string;
  lastCommandExitCode?: number;
  lastCommandPreview?: string;
  lastToolPath?: string;
  lastToolKind?: "reading" | "writing";
  lastAssistantText?: string;
  finalSummary?: string;
  changedFiles?: string[];
  transcriptPath?: string;
  lastEventAt?: number;
};

const USER_FACING_EVENT_TYPES = new Set([
  "stream_text",
  "phase",
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
  "verification_started",
  "verification_finished",
  "validation_started",
  "validation_finished",
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

function commandCategory(command: unknown): VillaniCopyCategory {
  const text = String(command || "");
  return /pytest|\btest\b|coverage|tox|unittest/i.test(text) ? "testing" : "running";
}

function pathFromEvent(event: any): string | undefined {
  const input = event.input && typeof event.input === "object" ? event.input : {};
  const value = event.path || event.file_path || event.filepath || event.filename || event.target_file || input.path || input.file_path || input.filepath || input.filename || input.target_file;
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function commandFromEvent(event: any): string | undefined {
  const input = event.input && typeof event.input === "object" ? event.input : {};
  const value = event.command || input.command;
  return typeof value === "string" && value.trim() ? value.slice(0, 500) : undefined;
}

function categoryForEvent(event: any): VillaniCopyCategory | undefined {
  const type = String(event.type || "");
  const phase = String(event.phase || "");
  const tool = String(event.tool || event.name || "");
  if (type === "model_request_started" || type === "proxy_request_started") return "thinking";
  if (type === "approval_required") return "approval";
  if (type === "phase" && /diagnosis|planning/i.test(phase)) return "analysis";
  if (type === "tool_started") {
    if (["Read", "GitStatus", "GitDiff", "GitLog"].includes(tool)) return "reading";
    if (["Write", "Patch", "Edit"].includes(tool)) return "writing";
    if (tool === "Bash") return commandCategory(commandFromEvent(event));
  }
  if (type === "command_started") return commandCategory(commandFromEvent(event));
  if (type === "command_finished" && Number(event.exit_code ?? 0) !== 0) return "debugging";
  if (type === "validation_started" || type === "verification_started") return "testing";
  if (type === "validation_finished" || type === "verification_finished") return "review";
  if (type === "run_failed") return "failure";
  if (type === "run_completed") return "complete";
  return undefined;
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
export function toolStartedMessage(event: any): string {
  const tool = String(event.tool || event.name || "tool");
  const path = pathFromEvent(event);
  if (tool === "GitStatus" || tool === "GitDiff" || tool === "GitLog" || tool === "Read") return path ? `Villani reads file. File nervous.\nFile: ${path}` : "Villani reads file. File nervous.";
  if (tool === "Write" || tool === "Edit") return path ? `Villani makes file obey.\nFile: ${path}` : "Villani makes file obey.";
  if (tool === "Patch") return path ? `Villanipatch imposed.\nFile: ${path}` : "Villanipatch imposed.";
  if (tool === "Bash") return commandFromEvent(event) ? `Command:\n${commandFromEvent(event)}` : "Villani gives command...";
  return "Villani is make plan...";
}

export function reduceVillaniUiState(
  state: VillaniUiState,
  event: any,
): VillaniUiState {
  const next = { ...state, lastEventAt: Date.now() };
  const applyStatus = (category: VillaniCopyCategory, detailKey?: string) => {
    const status = nextVillaniStatus(category, detailKey);
    if (status) next.phase = status;
  };
  if (event.type === "run_started") applyStatus("thinking", "run-started");
  else if (event.type === "model_request_started") {
    applyStatus("thinking", "model-request");
    next.lastCommand = undefined;
    next.lastCommandExitCode = undefined;
    next.lastCommandPreview = undefined;
  } else if (event.type === "proxy_request_started")
    applyStatus("thinking", "model-request");
  else if (event.type === "model_request_completed") {
    if (!state.phase) applyStatus("review", "model-completed");
  } else if (event.type === "proxy_request_completed") {
    // Keep existing status; proxy completion is heartbeat-like UI noise.
  } else if (event.type === "approval_required") applyStatus("approval", event.request_id || event.requestId);
  else if (event.type === "approval_resolved") applyStatus("thinking", "approval-resolved");
  else if (event.type === "tool_started") {
    const tool = String(event.tool || event.name || "");
    const category = categoryForEvent(event) || "thinking";
    applyStatus(category, `${tool}:${pathFromEvent(event) || commandFromEvent(event) || ""}`);
    next.lastToolPath = pathFromEvent(event);
    next.lastToolKind = ["Read", "GitStatus", "GitDiff", "GitLog"].includes(tool) ? "reading" : (["Write", "Patch", "Edit"].includes(tool) ? "writing" : undefined);
    if (tool === "Bash") {
      next.lastCommand = commandFromEvent(event);
      next.lastToolPath = undefined;
      next.lastToolKind = undefined;
    }
  }
  else if (event.type === "command_started") {
    const command = String(event.command || "").slice(0, 500);
    applyStatus(commandCategory(event.command), command);
    next.lastCommand = command;
    next.lastCommandExitCode = undefined;
    next.lastCommandPreview = undefined;
  } else if (event.type === "command_finished") {
    applyStatus(Number(event.exit_code ?? 0) !== 0 ? "debugging" : "review", String(event.command || next.lastCommand || ""));
    next.lastCommand = String(event.command || next.lastCommand || "").slice(
      0,
      500,
    );
    next.lastCommandExitCode = event.exit_code;
    next.lastCommandPreview = preview(event);
  } else if (event.type === "phase") {
    applyStatus(categoryForEvent(event) || "thinking", String(event.phase || ""));
  } else if (event.type === "verification_started" || event.type === "validation_started")
    applyStatus("testing", event.type);
  else if (event.type === "verification_finished" || event.type === "validation_finished")
    applyStatus("review", event.type);
  else if (event.type === "run_completed") {
    applyStatus("complete", "run-completed");
    next.finalSummary = event.summary;
    next.changedFiles = visibleChangedFiles(
      event.changed_files || event.changedFiles || next.changedFiles || [],
    );
    next.transcriptPath = event.transcript_path || event.transcriptPath;
  } else if (event.type === "run_failed") {
    applyStatus("failure", "run-failed");
    next.finalSummary = event.summary || event.error;
  } else if (event.type === "run_aborted") applyStatus("failure", "run-aborted");
  else if (
    event.type === "runner_heartbeat" &&
    Date.now() - (state.lastEventAt || 0) > 15000
  )
    applyStatus("thinking", "heartbeat");
  return next;
}
export function widgetForState(state: VillaniUiState): any {
  if (state.lastToolPath && state.lastToolKind)
    return ["File:", state.lastToolPath].join("\n");
  if (state.lastCommandExitCode !== undefined)
    return [
      `Command finished: exit ${state.lastCommandExitCode ?? "unknown"}`,
      state.lastCommandPreview || "",
    ]
      .filter(Boolean)
      .join("\n");
  if (state.lastCommand)
    return ["Command:", state.lastCommand].join("\n");
  if (state.changedFiles?.length)
    return `Changed files:\n${state.changedFiles.map((f) => `- ${f}`).join("\n")}`;
  return undefined;
}
export async function renderState(ctx: any, state: VillaniUiState) {
  if (state.phase && state.phase !== lastRenderedStatus) {
    await setStatus(ctx, state.phase);
    lastRenderedStatus = state.phase;
  }
  await setWidget(ctx, widgetForState(state));
}
let state: VillaniUiState = { phase: "" };
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
    await setWidget(ctx, ["Villaniclearance required", event.summary || event.message || ""]);
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
    await setWidget(ctx, widgetForState(state) || toolStartedMessage(event));
    return;
  }
  if (event.type === "command_started") {
    state = reduceVillaniUiState(state, event);
    await setStatus(ctx, state.phase);
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
export function resetVillaniUiState() {
  state = { phase: "" };
  resetVillaniStatusManager();
}
