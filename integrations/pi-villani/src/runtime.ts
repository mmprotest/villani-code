import { createHash } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { access, chmod, mkdir, mkdtemp, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import AdmZip from "adm-zip";
import * as tar from "tar";
import { RuntimeAsset, resolveRuntimeAsset, VILLANI_RUNTIME_VERSION } from "./runtimeConfig.js";

export interface ResolvedVillaniExecutable {
  executable: string;
  source: "override" | "cached-runtime" | "downloaded-runtime";
  version?: string;
}

export interface RuntimeResolverOptions {
  overrideCommand?: string;
  onProgress?: (message: string) => void;
  signal?: AbortSignal;
  platform?: NodeJS.Platform;
  arch?: string;
  cacheRoot?: string;
  fetchImpl?: typeof fetch;
  extractArchive?: (archivePath: string, destination: string, asset: RuntimeAsset) => Promise<void>;
}

interface VerificationMarker {
  runtimeVersion: string;
  assetName: string;
  checksum: string;
  installedAt: string;
}

export async function resolveVillaniExecutable(options: RuntimeResolverOptions = {}): Promise<ResolvedVillaniExecutable> {
  const override = options.overrideCommand?.trim();
  if (override) return { executable: override, source: "override" };

  const asset = resolveRuntimeAsset(options.platform, options.arch ?? process.arch);
  const cacheRoot = options.cacheRoot ?? defaultRuntimeCacheRoot();
  const finalDir = runtimeInstallDir(cacheRoot, asset.platformKey);
  const markerPath = join(finalDir, ".verified.json");
  const executable = join(finalDir, asset.executableRelativePath);
  if (await isVerifiedRuntime(markerPath, executable, asset)) {
    return { executable, source: "cached-runtime", version: VILLANI_RUNTIME_VERSION };
  }

  throwIfAborted(options.signal);
  options.onProgress?.(`Downloading Villani runtime for ${asset.platformKey}...`);
  await mkdir(dirname(finalDir), { recursive: true });
  const tempDir = await mkdtemp(join(dirname(finalDir), `.install-${asset.platformKey}-`));
  try {
    const fetchImpl = options.fetchImpl ?? fetch;
    const checksums = await fetchText(fetchImpl, asset.checksumsUrl, options.signal, "checksums.txt");
    throwIfAborted(options.signal);
    const expectedChecksum = parseChecksum(checksums, asset.assetName);
    const archiveBytes = await fetchBytes(fetchImpl, asset.downloadUrl, options.signal, asset.assetName);
    throwIfAborted(options.signal);
    const actualChecksum = sha256(archiveBytes);
    if (actualChecksum !== expectedChecksum) {
      throw new Error("Villani runtime download failed integrity verification and was not executed.");
    }

    const archivePath = join(tempDir, asset.assetName);
    const extractDir = join(tempDir, "extract");
    await mkdir(extractDir, { recursive: true });
    await writeFile(archivePath, archiveBytes);
    await (options.extractArchive ?? extractRuntimeArchive)(archivePath, extractDir, asset);
    const extractedExecutable = join(extractDir, asset.executableRelativePath);
    await assertExecutableExists(extractedExecutable, asset);
    if (asset.archiveType !== "zip") await chmod(extractedExecutable, 0o755);

    const marker: VerificationMarker = {
      runtimeVersion: VILLANI_RUNTIME_VERSION,
      assetName: asset.assetName,
      checksum: actualChecksum,
      installedAt: new Date().toISOString(),
    };
    await writeFile(join(extractDir, ".verified.json"), JSON.stringify(marker, null, 2), "utf8");
    await rm(finalDir, { recursive: true, force: true });
    try {
      await rename(extractDir, finalDir);
    } catch (error) {
      if (await isVerifiedRuntime(markerPath, executable, asset)) {
        return { executable, source: "cached-runtime", version: VILLANI_RUNTIME_VERSION };
      }
      throw error;
    }
    options.onProgress?.("Villani runtime installed.");
    return { executable, source: "downloaded-runtime", version: VILLANI_RUNTIME_VERSION };
  } catch (error) {
    if (isAbortError(error) || options.signal?.aborted) {
      throw new Error("Villani run cancelled during runtime setup.");
    }
    throw error;
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

export function defaultRuntimeCacheRoot(): string {
  if (process.env.VILLANI_RUNTIME_CACHE_DIR) return process.env.VILLANI_RUNTIME_CACHE_DIR;
  if (process.platform === "win32" && process.env.LOCALAPPDATA) return join(process.env.LOCALAPPDATA, "pi-villani", "runtime");
  return join(homedir(), ".cache", "pi-villani", "runtime");
}

export function runtimeInstallDir(cacheRoot: string, platformKey: string): string {
  return join(cacheRoot, VILLANI_RUNTIME_VERSION, platformKey);
}

export function parseChecksum(checksumsText: string, assetName: string): string {
  for (const rawLine of checksumsText.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const match = line.match(/^([a-fA-F0-9]{64})\s+\*?(.+)$/);
    if (match && match[2].trim() === assetName) return match[1].toLowerCase();
  }
  throw new Error(`Missing SHA-256 checksum for ${assetName}.`);
}

export function sha256(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

async function isVerifiedRuntime(markerPath: string, executable: string, asset: RuntimeAsset): Promise<boolean> {
  try {
    await access(executable, fsConstants.X_OK);
    const marker = JSON.parse(await readFile(markerPath, "utf8")) as Partial<VerificationMarker>;
    return marker.runtimeVersion === VILLANI_RUNTIME_VERSION && marker.assetName === asset.assetName && typeof marker.checksum === "string";
  } catch {
    return false;
  }
}

async function assertExecutableExists(path: string, asset: RuntimeAsset): Promise<void> {
  try {
    const info = await stat(path);
    if (!info.isFile()) throw new Error("not a file");
  } catch (error) {
    throw new Error(`Villani runtime archive is invalid: expected executable was not found at ${asset.executableRelativePath}.`, { cause: error });
  }
}

async function extractRuntimeArchive(archivePath: string, destination: string, asset: RuntimeAsset): Promise<void> {
  if (asset.archiveType === "zip") {
    new AdmZip(archivePath).extractAllTo(destination, true);
    return;
  }
  await tar.x({ file: archivePath, cwd: destination });
}

async function fetchText(fetchImpl: typeof fetch, url: string, signal: AbortSignal | undefined, label: string): Promise<string> {
  const response = await fetchImpl(url, { signal });
  if (!response.ok) throw new Error(`Villani could not download ${label} from GitHub Releases (HTTP ${response.status}).`);
  return response.text();
}

async function fetchBytes(fetchImpl: typeof fetch, url: string, signal: AbortSignal | undefined, label: string): Promise<Buffer> {
  const response = await fetchImpl(url, { signal });
  if (!response.ok) throw new Error(`Villani could not download ${label} from GitHub Releases (HTTP ${response.status}).`);
  return Buffer.from(await response.arrayBuffer());
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw new Error("Villani run cancelled during runtime setup.");
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && (error.name === "AbortError" || error.message.includes("aborted"));
}
