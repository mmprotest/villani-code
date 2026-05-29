export const VILLANI_RUNTIME_VERSION = "0.1.0";
export const VILLANI_RUNTIME_REPOSITORY = "mmprotest/villani-code";
export const VILLANI_RUNTIME_TAG = `pi-villani-runtime-v${VILLANI_RUNTIME_VERSION}`;

export type RuntimePlatformKey = "win32-x64" | "darwin-arm64" | "darwin-x64" | "linux-x64";

export interface RuntimeAsset {
  platformKey: RuntimePlatformKey;
  assetName: string;
  archiveType: "zip" | "tar.gz";
  executableRelativePath: string;
  downloadUrl: string;
  checksumsUrl: string;
}

export function resolveRuntimeAsset(platform: NodeJS.Platform = process.platform, arch: string = process.arch): RuntimeAsset {
  const key = `${platform}-${arch}`;
  if (!isSupportedRuntimePlatform(key)) {
    throw new Error(`Villani runtime is not yet available for platform ${key}. Set VILLANI_COMMAND to a locally installed Villani executable to continue.`);
  }
  const archiveType = platform === "win32" ? "zip" : "tar.gz";
  const suffix = archiveType === "zip" ? ".zip" : ".tar.gz";
  const assetName = `villani-runtime-v${VILLANI_RUNTIME_VERSION}-${key}${suffix}`;
  const base = `https://github.com/${VILLANI_RUNTIME_REPOSITORY}/releases/download/${VILLANI_RUNTIME_TAG}`;
  return {
    platformKey: key,
    assetName,
    archiveType,
    executableRelativePath: platform === "win32" ? "villani-code/villani-code.exe" : "villani-code/villani-code",
    downloadUrl: `${base}/${assetName}`,
    checksumsUrl: `${base}/checksums.txt`,
  };
}

function isSupportedRuntimePlatform(key: string): key is RuntimePlatformKey {
  return key === "win32-x64" || key === "darwin-arm64" || key === "darwin-x64" || key === "linux-x64";
}
