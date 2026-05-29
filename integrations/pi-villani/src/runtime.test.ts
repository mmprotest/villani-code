import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { resolveRuntimeAsset, VILLANI_RUNTIME_VERSION } from "./runtimeConfig.js";
import { parseChecksum, resolveVillaniExecutable, runtimeInstallDir, sha256 } from "./runtime.js";

function response(body: string | Buffer, status = 200): Response {
  const payload: BodyInit = typeof body === "string" ? body : new Uint8Array(body);
  return new Response(payload, { status });
}

async function tempRoot(): Promise<string> {
  return mkdtemp(join(tmpdir(), "pi-villani-runtime-test-"));
}

test("runtime platform mapping covers supported platforms", () => {
  assert.equal(resolveRuntimeAsset("win32", "x64").assetName, `villani-runtime-v${VILLANI_RUNTIME_VERSION}-win32-x64.zip`);
  assert.equal(resolveRuntimeAsset("win32", "x64").executableRelativePath, "villani-code/villani-code.exe");
  assert.equal(resolveRuntimeAsset("darwin", "arm64").platformKey, "darwin-arm64");
  assert.equal(resolveRuntimeAsset("darwin", "x64").platformKey, "darwin-x64");
  assert.equal(resolveRuntimeAsset("linux", "x64").platformKey, "linux-x64");
  assert.throws(() => resolveRuntimeAsset("linux", "arm64"), /not yet available/);
});

test("override returns exact command and skips downloads", async () => {
  const command = "C:\\Program Files\\Python\\Scripts\\villani-code.exe";
  const resolved = await resolveVillaniExecutable({
    overrideCommand: command,
    fetchImpl: async () => { throw new Error("should not fetch"); },
  });
  assert.deepEqual(resolved, { executable: command, source: "override" });
});

test("verified cached runtime is reused without downloading", async () => {
  const cacheRoot = await tempRoot();
  try {
    const asset = resolveRuntimeAsset("linux", "x64");
    const dir = runtimeInstallDir(cacheRoot, asset.platformKey);
    const executable = join(dir, asset.executableRelativePath);
    await mkdir(join(dir, "villani-code"), { recursive: true });
    await writeFile(executable, "#!/bin/sh\n", { mode: 0o755 });
    await writeFile(join(dir, ".verified.json"), JSON.stringify({ runtimeVersion: VILLANI_RUNTIME_VERSION, assetName: asset.assetName, checksum: "a".repeat(64) }), "utf8");
    const resolved = await resolveVillaniExecutable({ platform: "linux", arch: "x64", cacheRoot, fetchImpl: async () => { throw new Error("should not fetch"); } });
    assert.equal(resolved.source, "cached-runtime");
    assert.equal(resolved.executable, executable);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test("unverified cache is not executed and correct checksum installs runtime", async () => {
  const cacheRoot = await tempRoot();
  try {
    const asset = resolveRuntimeAsset("linux", "x64");
    const archive = Buffer.from("fake archive");
    const checksum = sha256(archive);
    let fetches = 0;
    const resolved = await resolveVillaniExecutable({
      platform: "linux",
      arch: "x64",
      cacheRoot,
      fetchImpl: async (url) => {
        fetches += 1;
        return String(url).endsWith("checksums.txt") ? response(`${checksum}  ${asset.assetName}\n`) : response(archive);
      },
      extractArchive: async (_archivePath, destination) => {
        await mkdir(join(destination, "villani-code"), { recursive: true });
        await writeFile(join(destination, asset.executableRelativePath), "#!/bin/sh\n", { mode: 0o755 });
      },
    });
    assert.equal(resolved.source, "downloaded-runtime");
    assert.equal(fetches, 2);
    const marker = JSON.parse(await readFile(join(runtimeInstallDir(cacheRoot, asset.platformKey), ".verified.json"), "utf8"));
    assert.equal(marker.checksum, checksum);

    const cached = await resolveVillaniExecutable({ platform: "linux", arch: "x64", cacheRoot, fetchImpl: async () => { throw new Error("should not refetch"); } });
    assert.equal(cached.source, "cached-runtime");
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test("checksum mismatch, non-200, missing executable, and abort fail safely", async () => {
  const cacheRoot = await tempRoot();
  try {
    const asset = resolveRuntimeAsset("linux", "x64");
    await assert.rejects(resolveVillaniExecutable({
      platform: "linux",
      arch: "x64",
      cacheRoot,
      fetchImpl: async (url) => String(url).endsWith("checksums.txt") ? response(`${"0".repeat(64)}  ${asset.assetName}\n`) : response(Buffer.from("bad")),
      extractArchive: async () => undefined,
    }), /integrity verification/);

    await assert.rejects(resolveVillaniExecutable({
      platform: "linux",
      arch: "x64",
      cacheRoot,
      fetchImpl: async () => response("nope", 404),
    }), /HTTP 404/);

    const archive = Buffer.from("archive");
    const checksum = sha256(archive);
    await assert.rejects(resolveVillaniExecutable({
      platform: "linux",
      arch: "x64",
      cacheRoot,
      fetchImpl: async (url) => String(url).endsWith("checksums.txt") ? response(`${checksum}  ${asset.assetName}\n`) : response(archive),
      extractArchive: async (_archivePath, destination) => { await mkdir(destination, { recursive: true }); },
    }), /expected executable was not found/);

    const controller = new AbortController();
    controller.abort();
    await assert.rejects(resolveVillaniExecutable({ platform: "linux", arch: "x64", cacheRoot, signal: controller.signal }), /cancelled during runtime setup/);
    await assert.rejects(stat(runtimeInstallDir(cacheRoot, asset.platformKey)), /ENOENT/);
  } finally {
    await rm(cacheRoot, { recursive: true, force: true });
  }
});

test("parseChecksum requires matching asset", () => {
  assert.equal(parseChecksum(`${"a".repeat(64)}  asset.zip\n`, "asset.zip"), "a".repeat(64));
  assert.throws(() => parseChecksum("", "asset.zip"), /Missing SHA-256/);
});
