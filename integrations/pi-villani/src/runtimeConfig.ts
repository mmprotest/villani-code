export const VILLANI_RUNTIME_VERSION = "0.1.3";
export const VILLANI_RUNTIME_REPOSITORY = "mmprotest/villani-code";
export const VILLANI_RUNTIME_TAG = `pi-villani-runtime-v${VILLANI_RUNTIME_VERSION}`;
export type PlatformKey = "win32-x64"|"darwin-arm64"|"darwin-x64"|"linux-x64";
export function platformKey(platform=process.platform, arch=process.arch): PlatformKey { const k=`${platform}-${arch}`; if(["win32-x64","darwin-arm64","darwin-x64","linux-x64"].includes(k)) return k as PlatformKey; throw new Error(`Unsupported platform: ${k}`); }
export function assetName(key: PlatformKey): string { return `villani-runtime-v${VILLANI_RUNTIME_VERSION}-${key}.${key.startsWith('win32')?'zip':'tar.gz'}`; }
export function executableRelativePath(key: PlatformKey): string { return key.startsWith('win32')?'villani-code/villani-code.exe':'villani-code/villani-code'; }
