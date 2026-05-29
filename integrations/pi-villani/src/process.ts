import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import { AbortCommand, BridgeCommand, BridgeEvent, commandToLine } from "./protocol.js";

export const DEFAULT_VILLANI_COMMAND = "villani-code";

export interface BridgeProcessOptions {
  command?: string;
  cwd: string;
  readyTimeoutMs?: number;
  env?: NodeJS.ProcessEnv;
}

interface CommandSpec {
  executable: string;
  args: string[];
  display: string;
  isFallback?: boolean;
}

export class VillaniBridgeProcess {
  private child: ChildProcessWithoutNullStreams;
  private stderrChunks: string[] = [];
  private listeners: Array<(event: BridgeEvent) => void> = [];
  private readyPromise: Promise<void>;
  private exitPromise: Promise<number | null>;
  private closed = false;
  private stdoutBuffer = "";
  private stdoutDecoder = new StringDecoder("utf8");
  private startupSettled = false;
  private startupReject?: (error: Error) => void;
  private startupResolve?: () => void;

  constructor(options: BridgeProcessOptions & { spec?: CommandSpec }) {
    const spec = options.spec ?? commandToSpec(options.command ?? DEFAULT_VILLANI_COMMAND);
    this.child = spawn(spec.executable, [...spec.args, "bridge", "--stdio"], {
      cwd: options.cwd,
      env: { ...process.env, ...options.env },
      shell: false,
      windowsHide: true,
    });

    this.readyPromise = new Promise<void>((resolve, reject) => {
      this.startupResolve = () => {
        this.startupSettled = true;
        resolve();
      };
      this.startupReject = (error: Error) => {
        this.startupSettled = true;
        reject(error);
      };
    });

    this.child.on("error", (error: NodeJS.ErrnoException) => {
      this.closed = true;
      this.rejectStartup(formatSpawnError(error, spec.display));
    });

    this.child.stderr.on("data", (chunk: Buffer) => {
      this.stderrChunks.push(chunk.toString("utf8"));
      if (this.stderrChunks.length > 20) this.stderrChunks.shift();
    });

    this.child.stdout.on("data", (chunk: Buffer) => {
      this.consumeStdout(this.stdoutDecoder.write(chunk));
    });
    this.child.stdout.on("end", () => {
      const tail = this.stdoutDecoder.end();
      if (tail) this.consumeStdout(tail);
      if (this.stdoutBuffer.trim()) this.failProtocol(`Incomplete bridge JSONL line before stdout closed: ${this.stdoutBuffer.slice(0, 200)}`);
    });

    this.exitPromise = new Promise((resolve) => {
      this.child.on("exit", (code, signal) => {
        this.closed = true;
        if (!this.startupSettled) {
          this.rejectStartup(new Error(`Villani bridge exited before ready (code ${code ?? "null"}, signal ${signal ?? "null"}).${this.stderrSuffix()}`));
        }
        resolve(code);
      });
    });

    const timeoutMs = options.readyTimeoutMs ?? 15_000;
    const timer = setTimeout(() => {
      if (!this.startupSettled) {
        this.rejectStartup(new Error(`Timed out waiting for Villani bridge ready after ${timeoutMs}ms.${this.stderrSuffix()}`));
        this.kill();
      }
    }, timeoutMs);
    this.readyPromise.finally(() => clearTimeout(timer)).catch(() => clearTimeout(timer));
  }

  onEvent(listener: (event: BridgeEvent) => void): void {
    this.listeners.push(listener);
  }

  waitUntilReady(): Promise<void> {
    return this.readyPromise;
  }

  waitForExit(): Promise<number | null> {
    return this.exitPromise;
  }

  send(command: BridgeCommand): void {
    if (this.closed || !this.child.stdin.writable) {
      throw new Error(`Villani bridge is not writable.${this.stderrSuffix()}`);
    }
    this.child.stdin.write(commandToLine(command));
  }

  abort(runId: string): void {
    const command: AbortCommand = { type: "abort", id: runId };
    this.send(command);
  }

  kill(): void {
    if (!this.closed) this.child.kill();
  }

  stderr(): string {
    return this.stderrChunks.join("").slice(-4000);
  }

  private consumeStdout(text: string): void {
    this.stdoutBuffer += text;
    while (true) {
      const newline = this.stdoutBuffer.indexOf("\n");
      if (newline < 0) return;
      const line = this.stdoutBuffer.slice(0, newline).trim();
      this.stdoutBuffer = this.stdoutBuffer.slice(newline + 1);
      if (!line) continue;
      let event: BridgeEvent;
      try {
        event = JSON.parse(line) as BridgeEvent;
      } catch {
        this.failProtocol(`Malformed bridge JSONL output: ${line.slice(0, 200)}`);
        return;
      }
      if (event.type === "ready") this.resolveStartup();
      this.listeners.forEach((listener) => listener(event));
    }
  }

  private failProtocol(message: string): void {
    const event: BridgeEvent = { type: "error", error: message };
    this.listeners.forEach((listener) => listener(event));
    this.rejectStartup(new Error(`${message}${this.stderrSuffix()}`));
    this.kill();
  }

  private resolveStartup(): void {
    if (!this.startupSettled) this.startupResolve?.();
  }

  private rejectStartup(error: Error): void {
    if (!this.startupSettled) this.startupReject?.(error);
  }

  private stderrSuffix(): string {
    const stderr = this.stderr().trim();
    return stderr ? ` stderr: ${stderr}` : "";
  }
}

export async function startVillaniBridgeProcess(options: BridgeProcessOptions): Promise<VillaniBridgeProcess> {
  if (options.command) {
    const bridgeProcess = new VillaniBridgeProcess(options);
    await bridgeProcess.waitUntilReady();
    return bridgeProcess;
  }

  const primarySpec = commandToSpec(DEFAULT_VILLANI_COMMAND);
  try {
    const bridgeProcess = new VillaniBridgeProcess({ ...options, spec: primarySpec });
    await bridgeProcess.waitUntilReady();
    return bridgeProcess;
  } catch (error) {
    if (!isExecutableMissingError(error)) throw error;
  }

  const fallbackSpec: CommandSpec = {
    executable: process.platform === "win32" ? "python" : "python3",
    args: ["-m", "villani_code.cli"],
    display: `${process.platform === "win32" ? "python" : "python3"} -m villani_code.cli`,
    isFallback: true,
  };
  const fallback = new VillaniBridgeProcess({ ...options, spec: fallbackSpec });
  await fallback.waitUntilReady();
  return fallback;
}

export function commandToSpec(command: string): CommandSpec {
  const trimmed = command.trim();
  if (!trimmed) return { executable: DEFAULT_VILLANI_COMMAND, args: [], display: DEFAULT_VILLANI_COMMAND };
  if (trimmed.startsWith("python -m ")) {
    const [, , moduleName, ...rest] = trimmed.split(/\s+/);
    return { executable: "python", args: ["-m", moduleName, ...rest], display: trimmed };
  }
  const parts = trimmed.split(/\s+/);
  return { executable: parts[0], args: parts.slice(1), display: trimmed };
}

function formatSpawnError(error: NodeJS.ErrnoException, display: string): Error {
  if (error.code === "ENOENT") {
    return new Error(
      `Unable to start Villani. The \`${display}\` executable was not found. Install Villani Code in the active environment or set VILLANI_COMMAND to the executable path.`,
    );
  }
  return new Error(`Unable to start Villani with \`${display}\`: ${error.message}`);
}

function isExecutableMissingError(error: unknown): boolean {
  return error instanceof Error && error.message.includes("executable was not found");
}
