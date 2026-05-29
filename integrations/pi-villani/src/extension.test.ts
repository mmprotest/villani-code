import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import villaniPiExtension from "./index.js";
import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";

async function mockAbortableBridge(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "villani-extension-"));
  const path = join(dir, "bridge.mjs");
  await writeFile(path, `
    process.stdout.write('{"type":"ready","protocol_version":1}\\n');
    process.stdin.setEncoding('utf8');
    let buffer = '';
    process.stdin.on('data', chunk => {
      buffer += chunk;
      for (;;) {
        const idx = buffer.indexOf('\\n');
        if (idx < 0) break;
        const line = buffer.slice(0, idx); buffer = buffer.slice(idx + 1);
        if (!line.trim()) continue;
        const msg = JSON.parse(line);
        if (msg.type === 'run') process.stdout.write(JSON.stringify({type:'run_started', id:msg.id, run_id:msg.id, task:msg.task, repo:msg.repo, mode:msg.mode}) + '\\n');
        if (msg.type === 'abort') { process.stdout.write(JSON.stringify({type:'run_aborted', id:msg.id, success:false, summary:'Aborted by test', changed_files:[], preexisting_dirty_files:[]}) + '\\n'); setTimeout(() => process.exit(0), 10); }
      }
    });
  `, "utf8");
  return `${process.execPath} ${path}`;
}

function createHost() {
  const commands = new Map<string, (args: string, ctx: ExtensionCommandContext) => Promise<void>>();
  const api = {
    registerCommand(name: string, options: { handler: (args: string, ctx: ExtensionCommandContext) => Promise<void> }) {
      commands.set(name, options.handler);
    },
  } as ExtensionAPI;
  return { api, commands };
}

function createContext(messages: string[]): ExtensionCommandContext {
  return {
    cwd: process.cwd(),
    model: undefined,
    signal: undefined,
    ui: {
      notify: (message: string) => messages.push(message),
      setStatus: (_key: string, text: string | undefined) => { if (text) messages.push(text); },
    },
  } as unknown as ExtensionCommandContext;
}

test("extension registers /villani and /villani-abort", () => {
  const host = createHost();
  villaniPiExtension(host.api);
  assert.equal(host.commands.has("villani"), true);
  assert.equal(host.commands.has("villani-abort"), true);
});

test("/villani-abort reports no active run", async () => {
  const host = createHost();
  const messages: string[] = [];
  villaniPiExtension(host.api);
  await host.commands.get("villani-abort")!("", createContext(messages));
  assert.match(messages.join("\n"), /No active Villani run/);
});

test("prevents overlapping runs and aborts active run", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  process.env.VILLANI_COMMAND = await mockAbortableBridge();
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    const ctx = createContext(messages);
    const runPromise = host.commands.get("villani")!("fix it", ctx);
    await new Promise((resolve) => setTimeout(resolve, 250));
    await host.commands.get("villani")!("second", ctx);
    assert.match(messages.join("\n"), /already running/);
    await host.commands.get("villani-abort")!("", ctx);
    await runPromise;
    assert.match(messages.join("\n"), /Villani aborted/);
  } finally {
    setOrDelete("VILLANI_COMMAND", oldCommand);
    setOrDelete("VILLANI_USE_PI_MODEL", oldUsePi);
    setOrDelete("VILLANI_PROVIDER", oldProvider);
    setOrDelete("VILLANI_MODEL", oldModel);
    setOrDelete("VILLANI_BASE_URL", oldBase);
  }
});

function setOrDelete(name: string, value: string | undefined): void {
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}
