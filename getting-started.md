# Getting Started with Villani Code

## 1) Run local-safe (default)

```bash
villani-code interactive --base-url http://127.0.0.1:1234 --model your-local-model
```

Defaults:

- `preset=local-safe`
- `small_model=true`
- strict planning/approval flow
- conservative context and execution budgets

## 2) Understand the runtime loop

Villani Code defaults to:

`plan -> approval -> execute -> checkpoint -> validate -> review`

This is designed for weak local models that need strict control.

## 3) Choose a preset intentionally

- `--preset local-safe` (default)
- `--preset local-fast`
- `--preset cloud-power` (explicit opt-in)

## 4) Roll back safely

```bash
villani-code rollback
```

or a specific checkpoint:

```bash
villani-code rollback --checkpoint-id <id>
```

## 5) Settings files

- User: `~/.villani/settings.json`
- Project: `.villani/settings.json`

Use settings to pin preferred defaults, but local-safe remains the product baseline.
