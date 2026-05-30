import { BridgeEvent } from "./protocol.js";

export interface PiLikeOutput {
  info?: (message: string) => void;
  warn?: (message: string) => void;
  error?: (message: string) => void;
  markdown?: (message: string) => void;
  log?: (message: string) => void;
}

export function renderEvent(event: BridgeEvent, output: PiLikeOutput = console): void {
  const write = output.info ?? output.log ?? console.log;
  const warn = output.warn ?? write;
  const error = output.error ?? warn;
  switch (event.type) {
    case "run_started":
      write(`Villani started: ${event.task}`);
      break;
    case "phase":
      write(`Villani: ${event.message}`);
      break;
    case "bridge_diagnostic":
      if (process.env.VILLANI_PI_DEBUG === "1") {
        write(`Villani debug: ${event.message}`);
      }
      break;
    case "tool_started":
      write(`Tool started: ${event.tool}${event.path ? ` ${event.path}` : ""}${event.command ? ` ${event.command}` : ""}`);
      break;
    case "tool_finished":
      write(`Tool ${event.ok ? "finished" : "failed"}: ${event.summary}`);
      break;
    case "workspace_changed":
      write(`Workspace changed: ${event.files.join(", ")}`);
      break;
    case "verification_started":
      write(`Verification started: ${event.command}`);
      break;
    case "verification_finished":
      write(`Verification ${event.passed ? "passed" : "failed"}: ${event.command}`);
      break;
    case "governor_redirect":
      warn(`Villani governor: ${event.message}`);
      break;
    case "run_completed":
    case "run_failed":
    case "run_aborted":
      renderFinalSummary(event, output);
      break;
    case "error":
      error(`Villani bridge error: ${event.error}`);
      break;
  }
}

export function renderFinalSummary(event: Extract<BridgeEvent, { type: "run_completed" | "run_failed" | "run_aborted" }>, output: PiLikeOutput = console): void {
  const markdown = output.markdown ?? output.info ?? output.log ?? console.log;
  const changed = "changed_files" in event && event.changed_files?.length ? event.changed_files : [];
  const preexisting = "preexisting_dirty_files" in event && event.preexisting_dirty_files?.length ? event.preexisting_dirty_files : [];
  const changedFiles = changed.length ? changed.map((file) => `- ${file}`).join("\n") : "None reported";
  const preexistingFiles = preexisting.length ? preexisting.map((file) => `- ${file}`).join("\n") : "";
  const verification = formatVerificationStatus("verification_passed" in event ? event.verification_passed : undefined);
  const status = event.type === "run_completed" ? "completed" : event.type === "run_aborted" ? "aborted" : "failed";
  markdown([
    `### Villani ${status}`,
    "",
    "**Summary:**",
    event.summary || "No summary reported.",
    "",
    `**Changed by Villani**\n${changedFiles}`,
    preexistingFiles ? `\n**Pre-existing workspace changes excluded from attribution**\n${preexistingFiles}` : "",
    "",
    `**Verification:** ${verification}`,
    `**Transcript:** ${event.transcript_path ?? "not reported"}`,
    "error" in event && event.error ? `**Error:** ${event.error}` : "",
  ].filter(Boolean).join("\n"));
}

export function formatVerificationStatus(value: boolean | null | undefined): "passed" | "failed" | "not reported" {
  if (value === true) return "passed";
  if (value === false) return "failed";
  return "not reported";
}
