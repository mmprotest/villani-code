import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { AbortCommand, BridgeCommand, BridgeEvent, commandToLine } from "./protocol";

export interface BridgeProcessOptions {
  command: string;
  cwd: string;
  readyTimeoutMs?: number;
  env?: NodeJS.ProcessEnv;
}

export class VillaniBridgeProcess {
  private child: ChildProcessWithoutNullStreams;
  private stderrChunks: string[] = [];
  private listeners: Array<(event: BridgeEvent) => void> = [];
  private readyPromise: Promise<void>;
  private exitPromise: Promise<number | null>;
  private closed = false;

  constructor(options: BridgeProcessOptions) {
    const { executable, args } = splitCommand(options.command);
    this.child = spawn(executable, [...args, "bridge", "--stdio"], {
      cwd: options.cwd,
      env: { ...process.env, ...options.env },
      shell: false,
      windowsHide: true,
    });

    this.child.stderr.on("data", (chunk: Buffer) => {
      this.stderrChunks.push(chunk.toString("utf8"));
      if (this.stderrChunks.length > 20) this.stderrChunks.shift();
    });

    const stdout = createInterface({ input: this.child.stdout, crlfDelay: Infinity });
    let markReady: (() => void) | undefined;
    const readySeen = new Promise<void>((resolve) => {
      markReady = resolve;
    });
    stdout.on("line", (line: string) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      let event: BridgeEvent;
      try {
        event = JSON.parse(trimmed) as BridgeEvent;
      } catch {
        event = { type: "error", error: `Malformed bridge output: ${trimmed.slice(0, 200)}` };
      }
      if (event.type === "ready") markReady?.();
      this.listeners.forEach((listener) => listener(event));
    });

    this.exitPromise = new Promise((resolve) => {
      this.child.on("exit", (code) => {
        this.closed = true;
        resolve(code);
      });
    });

    const timeoutMs = options.readyTimeoutMs ?? 15_000;
    this.readyPromise = Promise.race([
      readySeen,
      new Promise<void>((_, reject) =>
        setTimeout(() => reject(new Error(`Timed out waiting for Villani bridge ready. stderr: ${this.stderr()}`)), timeoutMs),
      ),
    ]);
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
      throw new Error(`Villani bridge is not writable. stderr: ${this.stderr()}`);
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
}

function splitCommand(command: string): { executable: string; args: string[] } {
  const trimmed = command.trim();
  if (!trimmed) return { executable: "villani", args: [] };
  if (trimmed.startsWith("python -m ")) {
    const [, , moduleName, ...rest] = trimmed.split(/\s+/);
    return { executable: "python", args: ["-m", moduleName, ...rest] };
  }
  const parts = trimmed.split(/\s+/);
  return { executable: parts[0], args: parts.slice(1) };
}
