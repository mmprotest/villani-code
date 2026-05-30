export const PROTOCOL_VERSION = 1;

export type VillaniMode = "runner" | "villani";

export interface BridgeConfig {
  provider?: string;
  model?: string;
  base_url?: string;
  api_key?: string;
  pi_model_proxy?: boolean;
}

export interface BridgeLimits {
  max_turns?: number;
}

export interface PingCommand {
  type: "ping";
  id: string;
}

export interface RunCommand {
  type: "run";
  id: string;
  task: string;
  repo: string;
  mode: VillaniMode;
  config?: BridgeConfig;
  limits?: BridgeLimits;
}

export interface AbortCommand {
  type: "abort";
  id: string;
}

export interface ApprovalResponseCommand {
  type: "approval_response";
  id: string;
  request_id: string;
  approved: boolean;
}

export type BridgeCommand = PingCommand | RunCommand | AbortCommand | ApprovalResponseCommand;

export type BridgeEvent =
  | { type: "ready"; protocol_version: number }
  | { type: "pong"; id: string }
  | { type: "run_started"; id: string; run_id: string; task: string; repo: string; mode: VillaniMode }
  | { type: "phase"; id: string; phase: string; message: string }
  | { type: "bridge_diagnostic"; id?: string; message: string }
  | { type: "tool_started"; id: string; tool: string; path?: string | null; command?: string | null }
  | { type: "tool_finished"; id: string; tool: string; ok: boolean; summary: string }
  | { type: "workspace_changed"; id: string; files: string[] }
  | { type: "verification_started"; id: string; command: string }
  | { type: "verification_finished"; id: string; command: string; passed: boolean; summary: string }
  | { type: "governor_redirect"; id: string; message: string }
  | { type: "abort_requested"; id: string }
  | { type: "approval_required"; id: string; request_id: string; tool: string; summary: string; input: Record<string, unknown> }
  | { type: "approval_resolved"; id: string; request_id: string; tool: string; approved: boolean }
  | { type: "run_completed"; id: string; success: true; changed_files: string[]; preexisting_dirty_files?: string[]; verification_passed: boolean | null; summary: string; transcript_path?: string | null }
  | { type: "run_failed"; id: string; success: false; error: string; summary: string; changed_files?: string[]; preexisting_dirty_files?: string[]; transcript_path?: string | null }
  | { type: "run_aborted"; id: string; success: false; summary: string; changed_files?: string[]; preexisting_dirty_files?: string[]; transcript_path?: string | null }
  | { type: "error"; id?: string; error: string };

export function commandToLine(command: BridgeCommand): string {
  return `${JSON.stringify(command)}\n`;
}
