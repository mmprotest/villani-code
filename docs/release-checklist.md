# Release Checklist

Use this checklist before cutting a release candidate.

## 1) Targeted test pass

- Run benchmark-focused tests.
- Run headless optional-TUI tests.
- Run packaging/import surface tests.

## 2) Packaging smoke

- Build wheel/sdist (`python -m build`).
- Install wheel into a clean venv.
- Verify:
  - `import villani_code` succeeds
  - CLI entry point (`villani-code --help`) works
  - removed legacy surface (`ui`) is absent

## 3) Benchmark sanity check

- Run at least one benchmark pack end to end.
- Confirm report includes interpretation status and provenance summary.
- Confirm non-headline runs include a visible warning banner.

## 4) Install instructions sanity check

- Verify README install tiers (`.`, `.[tui]`, `.[dev]`) are accurate.
- Confirm optional-TUI failure message matches documented guidance.

## 5) Windows validation sanity check

- Confirm validation-command normalization is explicit:
  - `python`/`python3`/`py` use active interpreter
  - allowlisted module tools resolve via `python -m`
  - unknown commands are not silently rewritten
