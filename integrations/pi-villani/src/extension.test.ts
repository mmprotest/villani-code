import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { Model } from "@earendil-works/pi-ai";
import villaniPiExtension from "./index.js";
import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";

async function mockNodeBridgeExecutable(exitAfterRun = false): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "villani-extension-"));
  const executable = join(dir, process.platform === "win32" ? "villani-mock.cmd" : "villani-mock");
  const modulePath = join(dir, "bridge.mjs");
  await writeFile(modulePath, `
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
        if (msg.type === 'run') {
          process.stdout.write(JSON.stringify({type:'run_started', id:msg.id, run_id:msg.id, task:msg.task, repo:msg.repo, mode:msg.mode}) + '\\n');
          if (${JSON.stringify(exitAfterRun)}) { process.stdout.write(JSON.stringify({type:'run_completed', id:msg.id, success:true, changed_files:[], preexisting_dirty_files:[], verification_passed:null, summary:'done', transcript_path:null}) + '\\n'); setTimeout(() => process.exit(0), 10); }
        }
        if (msg.type === 'abort') { process.stdout.write(JSON.stringify({type:'run_aborted', id:msg.id, success:false, summary:'Aborted by test', changed_files:[], preexisting_dirty_files:[]}) + '\\n'); setTimeout(() => process.exit(0), 10); }
      }
    });
  `, "utf8");
  if (process.platform === "win32") {
    await writeFile(executable, `@echo off\n"${process.execPath}" "${modulePath}" %*\n`, "utf8");
  } else {
    await writeFile(executable, `#!/usr/bin/env sh\nexec "${process.execPath}" "${modulePath}" "$@"\n`, { encoding: "utf8", mode: 0o755 });
  }
  return executable;
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

function fakeModel(): Model<string> {
  return {
    id: "pi-test",
    name: "Pi Test",
    api: "openai-completions",
    provider: "pi",
    baseUrl: "pi://current",
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 4096,
  } as Model<string>;
}

function createContext(messages: string[], options: { model?: Model<string>; authDelayMs?: number; signal?: AbortSignal } = {}): ExtensionCommandContext {
  return {
    cwd: process.cwd(),
    model: options.model,
    signal: options.signal,
    modelRegistry: {
      getApiKeyAndHeaders: async (model: Model<string>) => {
        if (options.authDelayMs) await new Promise((resolve) => setTimeout(resolve, options.authDelayMs));
        messages.push(`auth:${model.id}`);
        return { ok: true, apiKey: "pi-secret", headers: { Authorization: "Bearer pi-token" } };
      },
    },
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

test("prevents overlapping runs and aborts active bridge run", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  process.env.VILLANI_COMMAND = await mockNodeBridgeExecutable(false);
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
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
  }
});

test("Pi model path resolves model auth", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  process.env.VILLANI_COMMAND = await mockNodeBridgeExecutable(true);
  delete process.env.VILLANI_USE_PI_MODEL;
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages, { model: fakeModel() }));
    assert.equal(messages.includes("auth:pi-test"), true);
    assert.match(messages.join("\n"), /Villani completed/);
  } finally {
    setOrDelete("VILLANI_COMMAND", oldCommand);
    setOrDelete("VILLANI_USE_PI_MODEL", oldUsePi);
  }
});

test("explicit Villani config does not resolve Pi credentials", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  process.env.VILLANI_COMMAND = await mockNodeBridgeExecutable(true);
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages, { model: fakeModel() }));
    assert.equal(messages.some((line) => line.startsWith("auth:")), false);
  } finally {
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
  }
});

test("abort during startup is recognized before bridge exists", async () => {
  const host = createHost();
  const messages: string[] = [];
  villaniPiExtension(host.api);
  const ctx = createContext(messages, { model: fakeModel(), authDelayMs: 500 });
  const runPromise = host.commands.get("villani")!("fix it", ctx);
  await new Promise((resolve) => setTimeout(resolve, 50));
  await host.commands.get("villani-abort")!("", ctx);
  await runPromise;
  assert.doesNotMatch(messages.join("\n"), /No active Villani run/);
  assert.match(messages.join("\n"), /cancelled/);
});

function restoreEnv(values: { oldCommand?: string; oldUsePi?: string; oldProvider?: string; oldModel?: string; oldBase?: string }): void {
  setOrDelete("VILLANI_COMMAND", values.oldCommand);
  setOrDelete("VILLANI_USE_PI_MODEL", values.oldUsePi);
  setOrDelete("VILLANI_PROVIDER", values.oldProvider);
  setOrDelete("VILLANI_MODEL", values.oldModel);
  setOrDelete("VILLANI_BASE_URL", values.oldBase);
}

function setOrDelete(name: string, value: string | undefined): void {
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}
