# Releasing pi-villani

The Pi extension and the standalone Villani runtime currently share version `0.1.0`.

1. Update `integrations/pi-villani/package.json` and `integrations/pi-villani/src/runtimeConfig.ts` to the new version.
2. Push a tag named `pi-villani-runtime-vX.Y.Z`.
3. Confirm GitHub Actions workflow `Build pi-villani runtime release` builds and smoke-tests every supported runtime archive.
4. Confirm the GitHub Release contains:
   - `villani-runtime-vX.Y.Z-win32-x64.zip`
   - `villani-runtime-vX.Y.Z-darwin-arm64.tar.gz`
   - `villani-runtime-vX.Y.Z-darwin-x64.tar.gz`
   - `villani-runtime-vX.Y.Z-linux-x64.tar.gz`
   - `checksums.txt`
5. Run `cd integrations/pi-villani && npm ci && npm run build && npm test && npm pack --dry-run`.
6. Publish the npm package only after the runtime assets exist for the version referenced by `VILLANI_RUNTIME_VERSION`.
7. In a clean Pi environment, run `pi install npm:pi-villani`, open Pi in a repository, and smoke-test `/villani` plus `/villani-abort`.

Do not publish an npm package that references runtime assets that have not been uploaded yet.
