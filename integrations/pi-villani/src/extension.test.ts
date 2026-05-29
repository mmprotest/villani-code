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
    hasUI: true,
    ui: {
      notify: (message: string) => messages.push(message),
      confirm: async (title: string, message: string) => { messages.push(`confirm:${title}:${message}`); return false; },
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
    assert.match(messages.join("\n"), /Villani aborted/);
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
    assert.match(messages.join("\n"), /Villani completed/);
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
    assert.match(messages.join("\n"), /Villani completed/);
    assert.equal(bridgeCalls[0]?.command, cachedRuntime.executable);
  } finally {
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("VILLANI_RUNTIME_CACHE_DIR", oldCache);
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


test("approval event is confirmed and answered", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const oldProvider = process.env.VILLANI_PROVIDER;
  const oldModel = process.env.VILLANI_MODEL;
  const oldBase = process.env.VILLANI_BASE_URL;
  const oldApproval = process.env.MOCK_APPROVAL;
  const restorePrompt = __setApprovalPrompterForTests(async (request) => {
    assert.equal(request.request_id, "approval-1");
    assert.equal(request.input.path, "safe-test.txt");
    return true;
  });
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
    await host.commands.get("villani")!("fix it", createContext(messages));
    assert.match(messages.join("\n"), /Approved Villani Write request/);
    assert.match(messages.join("\n"), /approval_resolved|Villani completed/);
  } finally {
    restorePrompt();
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});

test("rejected approval and UI failure default to denial", async () => {
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
  let restorePrompt = __setApprovalPrompterForTests(async () => false);
  try {
    const host = createHost();
    const messages: string[] = [];
    villaniPiExtension(host.api);
    await host.commands.get("villani")!("fix it", createContext(messages));
    restorePrompt();
    assert.match(messages.join("\n"), /Denied Villani Write request/);

    restorePrompt = __setApprovalPrompterForTests(async () => { throw new Error("dialog missing"); });
    const host2 = createHost();
    const messages2: string[] = [];
    villaniPiExtension(host2.api);
    await host2.commands.get("villani")!("fix it", createContext(messages2));
    assert.match(messages2.join("\n"), /Approval UI unavailable/);
    assert.match(messages2.join("\n"), /Denied Villani Write request/);
  } finally {
    restorePrompt();
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
    assert.match(messages.join("\n"), /Villani aborted|cancelled/);
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
    assert.match(messages.join("\n"), /Approved Villani Write request/);
    assert.match(messages.join("\n"), /Denied Villani Patch request/);
  } finally {
    restorePrompt();
    restoreBridge();
    restoreEnv({ oldCommand, oldUsePi, oldProvider, oldModel, oldBase });
    setOrDelete("MOCK_APPROVAL", oldApproval);
  }
});
