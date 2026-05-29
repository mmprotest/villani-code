import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { commandToSpec, DEFAULT_VILLANI_COMMAND, VillaniBridgeProcess } from "./process.js";

async function mockBridgeSpec(script: string) {
  const dir = await mkdtemp(join(tmpdir(), "villani-bridge-"));
  const modulePath = join(dir, "bridge.mjs");
  await writeFile(modulePath, script, "utf8");
  return {
    executable: process.execPath,
    args: [modulePath],
    display: process.execPath,
  };
}

test("default command is villani-code", () => {
  assert.equal(DEFAULT_VILLANI_COMMAND, "villani-code");
  assert.deepEqual(commandToSpec(""), { executable: "villani-code", args: [], display: "villani-code" });
});

test("VILLANI_COMMAND path with spaces is treated as one executable", () => {
  const command = "C:\\Program Files\\Python\\Scripts\\villani-code.exe";
  assert.deepEqual(commandToSpec(command), { executable: command, args: [], display: command });
});

test("reports missing executable without unhandled process error", async () => {
  const bridge = new VillaniBridgeProcess({ command: "definitely-not-real-villani-command", cwd: process.cwd(), readyTimeoutMs: 500 });
  await assert.rejects(bridge.waitUntilReady(), /executable was not found/);
});

test("rejects when bridge exits before ready", async () => {
  const spec = await mockBridgeSpec("process.stderr.write('bad startup'); process.exit(7);\n");
  const bridge = new VillaniBridgeProcess({ spec, cwd: process.cwd(), readyTimeoutMs: 1000 });
  await assert.rejects(bridge.waitUntilReady(), /exited before ready.*bad startup/);
});

test("rejects malformed bridge output", async () => {
  const spec = await mockBridgeSpec("process.stdout.write('not json\\n'); setTimeout(() => {}, 1000);\n");
  const bridge = new VillaniBridgeProcess({ spec, cwd: process.cwd(), readyTimeoutMs: 1000 });
  await assert.rejects(bridge.waitUntilReady(), /Malformed bridge JSONL output/);
});

test("processes successful ready handshake", async () => {
  const spec = await mockBridgeSpec("process.stdout.write('{\"type\":\"ready\",\"protocol_version\":1}\\n'); setTimeout(() => process.exit(0), 50);\n");
  const bridge = new VillaniBridgeProcess({ spec, cwd: process.cwd(), readyTimeoutMs: 1000 });
  await bridge.waitUntilReady();
  assert.equal(await bridge.waitForExit(), 0);
});

test("exit during active run is observable", async () => {
  const spec = await mockBridgeSpec("process.stdout.write('{\"type\":\"ready\",\"protocol_version\":1}\\n'); setTimeout(() => process.exit(3), 50);\n");
  const bridge = new VillaniBridgeProcess({ spec, cwd: process.cwd(), readyTimeoutMs: 1000 });
  await bridge.waitUntilReady();
  assert.equal(await bridge.waitForExit(), 3);
});
