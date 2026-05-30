import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { Model } from "@earendil-works/pi-ai";
import villaniPiExtension, { __setApprovalPrompterForTests, __setBridgeStarterForTests } from "./index.js";
import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import { VillaniBridgeProcess, type BridgeProcessOptions } from "./process.js";
import { resolveRuntimeAsset, VILLANI_RUNTIME_VERSION } from "./runtimeConfig.js";

async function mockNodeBridgeModule(exitAfterRun = false): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "villani-extension-"));
  const modulePath = join(dir, "bridge.mjs");
  await writeFile(modulePath, `
    process.stdout.write('{"type":"ready","protocol_version":1}\\n');
    process.stdin.setEncoding('utf8');
    let buffer = '';
    const approvalResponses = new Map();
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
          if (process.env.MOCK_APPROVAL === '1') {
            process.stdout.write(JSON.stringify({type:'approval_required', id:msg.id, request_id:'approval-1', tool:'Write', summary:'Write file: safe-test.txt', input:{path:'safe-test.txt'}}) + '\\n');
            continue;
          }
          if (process.env.MOCK_APPROVAL === '2') {
            process.stdout.write(JSON.stringify({type:'approval_required', id:msg.id, request_id:'approval-1', tool:'Write', summary:'Write file: one.txt', input:{path:'one.txt'}}) + '\\n');
            continue;
          }
          if (process.env.MOCK_APPROVAL === 'BASH') {
            process.stdout.write(JSON.stringify({type:'approval_required', id:msg.id, request_id:'approval-bash', tool:'Bash', summary:'Run command: pip install package-name', input:{command:'pip install package-name'}}) + '\\n');
            continue;
          }
          if (${JSON.stringify(exitAfterRun)}) { process.stdout.write(JSON.stringify({type:'run_completed', id:msg.id, success:true, changed_files:[], preexisting_dirty_files:[], verification_passed:null, summary:'done', transcript_path:null}) + '\\n'); setTimeout(() => process.exit(0), 10); }
        }
        if (msg.type === 'approval_response') {
          const count = (approvalResponses.get(msg.request_id) || 0) + 1;
          approvalResponses.set(msg.request_id, count);
          process.stdout.write(JSON.stringify({type:'phase', id:msg.id, phase:'test', message:'approval_response:' + msg.request_id + ':' + msg.approved + ':count:' + count}) + '\\n');
          process.stdout.write(JSON.stringify({type:'approval_resolved', id:msg.id, request_id:msg.request_id, tool: msg.request_id === 'approval-2' ? 'Patch' : 'Write', approved:msg.approved}) + '\\n');
          if (process.env.MOCK_APPROVAL === '2' && msg.request_id === 'approval-1') {
            process.stdout.write(JSON.stringify({type:'approval_required', id:msg.id, request_id:'approval-2', tool:'Patch', summary:'Apply patch to: two.txt', input:{path:'two.txt'}}) + '\\n');
            process.env.MOCK_APPROVAL = 'DONE';
            continue;
          }
          process.stdout.write(JSON.stringify({type:'run_completed', id:msg.id, success:true, changed_files:[], preexisting_dirty_files:[], verification_passed:null, summary:'done', transcript_path:null}) + '\\n');
          setTimeout(() => process.exit(0), 10);
        }
        if (msg.type === 'abort') { process.stdout.write(JSON.stringify({type:'run_aborted', id:msg.id, success:false, summary:'Aborted by test', changed_files:[], preexisting_dirty_files:[]}) + '\\n'); setTimeout(() => process.exit(0), 10); }
      }
    });
  `, "utf8");
  return modulePath;
}

function installMockBridgeStarter(modulePath: string, calls: BridgeProcessOptions[] = []): () => void {
  return __setBridgeStarterForTests(async (options) => {
    calls.push(options);
    const bridge = new VillaniBridgeProcess({
      spec: {
        executable: process.execPath,
        args: [modulePath],
        display: process.execPath,
      },
      cwd: options.cwd,
      env: options.env,
      signal: options.signal,
      readyTimeoutMs: options.readyTimeoutMs ?? 1000,
    });
    await bridge.waitUntilReady();
    return bridge;
  });
}

async function installCachedRuntimeBridge(): Promise<{ cacheRoot: string; executable: string }> {
  const cacheRoot = await mkdtemp(join(tmpdir(), "villani-runtime-cache-"));
  const asset = resolveRuntimeAsset();
  const finalDir = join(cacheRoot, VILLANI_RUNTIME_VERSION, asset.platformKey);
  const runtimeExecutable = join(finalDir, asset.executableRelativePath);
  const runtimeDir = join(finalDir, "villani-code");
  await mkdir(runtimeDir, { recursive: true });
  await writeFile(runtimeExecutable, "test placeholder; not executed\n", "utf8");
  if (process.platform !== "win32") await chmod(runtimeExecutable, 0o755);
  await writeFile(join(finalDir, ".verified.json"), JSON.stringify({ runtimeVersion: VILLANI_RUNTIME_VERSION, assetName: asset.assetName, checksum: "a".repeat(64) }), "utf8");
  return { cacheRoot, executable: runtimeExecutable };
}

async function waitForCondition(predicate: () => boolean, timeoutMs = 3000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("Timed out waiting for test condition.");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

function createHost() {
  const commands = new Map<string, (args: string, ctx: ExtensionCommandContext) => Promise<void>>();
  const sentMessages: Array<{ content?: unknown }> = [];
  const api = {
    registerCommand(name: string, options: { handler: (args: string, ctx: ExtensionCommandContext) => Promise<void> }) {
      commands.set(name, options.handler);
    },
    sendMessage(message: { content?: unknown }) {
      sentMessages.push(message);
    },
  } as unknown as ExtensionAPI;
  return { api, commands, sentMessages };
}

function sentMessageText(host: ReturnType<typeof createHost>): string {
  return host.sentMessages
    .map((message) => typeof message.content === "string" ? message.content : String(message.content ?? ""))
    .join("\n");
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

function createContext(messages: string[], options: { model?: Model<string>; authDelayMs?: number; signal?: AbortSignal; throwStatusMatching?: RegExp; throwWidgetMatching?: RegExp; confirmResult?: boolean; confirmError?: Error } = {}): ExtensionCommandContext {
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
    hasUI: true,
    ui: {
      notify: (message: string) => messages.push(message),
      confirm: async (title: string, message: string) => {
        messages.push(`confirm:${title}:${message}`);
        if (options.confirmError) throw options.confirmError;
        return options.confirmResult ?? false;
      },
      setStatus: (_key: string, text: string | undefined) => {
        const rendered = text ? `status:${text}` : "status:clear";
        if (text && options.throwStatusMatching?.test(text)) throw new Error("status failed");
        messages.push(rendered);
        if (text) messages.push(text);
      },
      setWidget: (_key: string, lines: string[] | undefined, widgetOptions?: { placement?: string }) => {
        const rendered = lines ? `widget:${lines.join("|")}:placement:${widgetOptions?.placement ?? "none"}` : "widget:clear";
        if (lines && options.throwWidgetMatching?.test(lines.join("\n"))) throw new Error("widget failed");
        messages.push(rendered);
      },
    },
  } as unknown as ExtensionCommandContext;
}

test("extension registers /villani, /villani-abort, and /villani-confirm-test", () => {
  const host = createHost();
  villaniPiExtension(host.api);
  assert.equal(host.commands.has("villani"), true);
  assert.equal(host.commands.has("villani-abort"), true);
  assert.equal(host.commands.has("villani-confirm-test"), true);
});


test("/villani-confirm-test uses ctx.ui.confirm", async () => {
  const host = createHost();
  const messages: string[] = [];
  villaniPiExtension(host.api);
  await host.commands.get("villani-confirm-test")!("", createContext(messages, { confirmResult: true }));
  const joined = messages.join("\n");
  assert.match(joined, /confirm:Villani confirmation smoke test/);
  assert.match(joined, /Villani confirm smoke test: approved/);
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
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
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
    await waitForCondition(() => messages.some((message) => /Villani started/.test(message)));
    await host.commands.get("villani")!("second", ctx);
    assert.match(messages.join("\n"), /already running/);
    await host.commands.get("villani-abort")!("", ctx);
    await runPromise;
    assert.match(sentMessageText(host), /Villani aborted/);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
  }
});

test("Pi model path resolves model auth", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(true));
  process.env.VILLANI_COMMAND = "mock-villani";
  delete process.env.VILLANI_USE_PI_MODEL;
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages, { model: fakeModel() }));
    assert.equal(messages.includes("auth:pi-test"), true);
    assert.match(sentMessageText(host), /Villani completed/);
  } finally {
    restoreBridge();
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
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(true));
  process.env.VILLANI_COMMAND = "mock-villani";
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
    restoreBridge();
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


test("default path uses cached downloaded runtime before launching bridge", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldCache = process.env.VILLANI_RUNTIME_CACHE_DIR;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const bridgeCalls: BridgeProcessOptions[] = [];
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(true), bridgeCalls);
  const cachedRuntime = await installCachedRuntimeBridge();
  process.env.VILLANI_RUNTIME_CACHE_DIR = cachedRuntime.cacheRoot;
  delete process.env.VILLANI_COMMAND;
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages));
    assert.match(sentMessageText(host), /Villani completed/);
    assert.equal(bridgeCalls[0]?.command, cachedRuntime.executable);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("VILLANI_RUNTIME_CACHE_DIR", oldCache);
  }
});



test("startup and final events update visible persistent UI", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(true));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages));
    const joined = messages.join("\n");
    assert.match(joined, /status:Villani: starting/);
    assert.match(joined, /status:Villani: starting model proxy/);
    assert.match(joined, /status:Villani: starting runtime/);
    assert.match(joined, /status:Villani: running/);
    assert.match(joined, /status:clear/);
    assert.match(joined, /widget:clear/);
    assert.match(sentMessageText(host), /done/);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
  }
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


function approvalResponseLines(messages: string[], requestId = "approval-1"): string[] {
  return messages.filter((message) => message.includes(`approval_response:${requestId}:`));
}

function assertOneApprovalResponse(messages: string[], approved: boolean, requestId = "approval-1"): void {
  assert.deepEqual(approvalResponseLines(messages, requestId), [`Villani: approval_response:${requestId}:${approved}:count:1`]);
}

test("approval event is confirmed and answered", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  process.env.MOCK_APPROVAL = "1";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages, { confirmResult: true }));
    const joined = messages.join("\n");
    assert.match(joined, /status:Villani: awaiting approval for Write/);
    assert.match(joined, /widget:Villani is awaiting approval\|Write file: safe-test.txt:placement:aboveEditor/);
    assert.match(joined, /confirm:Villani wants to write a file/);
    assert.match(joined, /File: safe-test.txt/);
    assert.doesNotMatch(joined, /Approved Villani Write request/);
    assertOneApprovalResponse(messages, true);
    assert.match(sentMessageText(host), /Villani completed/);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});

test("approval confirmation denial sends one denial response", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  process.env.MOCK_APPROVAL = "1";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages, { confirmResult: false }));
    assert.match(messages.join("\n"), /confirm:Villani wants to write a file/);
    assert.match(messages.join("\n"), /Denied Villani Write request/);
    assert.match(messages.join("\n"), /status:Villani: denied Write approval/);
    assert.match(messages.join("\n"), /widget:Villani approval denied\|Write file: safe-test.txt:placement:aboveEditor/);
    assertOneApprovalResponse(messages, false);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});

test("approval status rendering failure sends one denial response", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  process.env.MOCK_APPROVAL = "1";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages, { throwStatusMatching: /awaiting approval/, confirmError: new Error("confirmation should not open after status failure") }));
    assert.match(messages.join("\n"), /Approval UI failed; denied Write request. status failed/);
    assertOneApprovalResponse(messages, false);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});

test("approval widget rendering failure sends one denial response", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  process.env.MOCK_APPROVAL = "1";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages, { throwWidgetMatching: /awaiting approval/, confirmError: new Error("confirmation should not open after widget failure") }));
    assert.match(messages.join("\n"), /Approval UI failed; denied Write request. widget failed/);
    assertOneApprovalResponse(messages, false);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});

test("approval confirmation failure sends one denial response", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  process.env.MOCK_APPROVAL = "1";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages, { confirmError: new Error("dialog missing") }));
    assert.match(messages.join("\n"), /confirm:Villani wants to write a file/);
    assert.match(messages.join("\n"), /Approval UI failed; denied Write request. dialog missing/);
    assert.match(messages.join("\n"), /Denied Villani Write request/);
    assertOneApprovalResponse(messages, false);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});

test("approval prompt content includes path or command", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  process.env.MOCK_APPROVAL = "BASH";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages));
    assert.match(messages.join("\n"), /confirm:Villani wants to run a shell command/);
    assert.match(messages.join("\n"), /pip install package-name/);
    assert.doesNotMatch(messages.join("\n"), /pi-secret|pi-token/);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});

test("abort during pending approval sends denial and abort", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  let unblock!: (value: boolean) => void;
  const restorePrompt = __setApprovalPrompterForTests(async () => new Promise<boolean>((resolve) => { unblock = resolve; }));
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  process.env.MOCK_APPROVAL = "1";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    const ctx = createContext(messages);
    const runPromise = host.commands.get("villani")!("fix it", ctx);
    await waitForCondition(() => typeof unblock === "function");
    await host.commands.get("villani-abort")!("", ctx);
    unblock(true);
    await runPromise;
    const responses = approvalResponseLines(messages);
    assert.ok(responses.length <= 1, `expected at most one approval response, got ${responses.join(", ")}`);
    if (responses.length === 1) assert.deepEqual(responses, ["Villani: approval_response:approval-1:false:count:1"]);
    assert.match(messages.join("\n"), /status:clear/);
    assert.doesNotMatch(messages.join("\n"), /Approved Villani Write request/);
  } finally {
    restorePrompt();
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});

test("multiple sequential approvals keep request ids distinct", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  const seen: string[] = [];
  const restorePrompt = __setApprovalPrompterForTests(async (request) => {
    seen.push(request.request_id);
    return request.request_id === "approval-1";
  });
  const restoreBridge = installMockBridgeStarter(await mockNodeBridgeModule(false));
  process.env.VILLANI_COMMAND = "mock-villani";
  process.env.VILLANI_USE_PI_MODEL = "false";
  process.env.VILLANI_PROVIDER = "openai";
  process.env.VILLANI_MODEL = "fake";
  process.env.VILLANI_BASE_URL = "http://127.0.0.1:9";
  process.env.MOCK_APPROVAL = "2";
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages));
    assert.deepEqual(seen, ["approval-1", "approval-2"]);
    assert.doesNotMatch(messages.join("\n"), /Approved Villani Write request/);
    assert.match(messages.join("\n"), /Denied Villani Patch request/);
    assertOneApprovalResponse(messages, true, "approval-1");
    assertOneApprovalResponse(messages, false, "approval-2");
  } finally {
    restorePrompt();
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});
