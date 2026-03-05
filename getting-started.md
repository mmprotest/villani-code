# Getting Started with Villani Code

## Quick start

1. Install dependencies.
2. Run `villani-code interactive --base-url ... --model ...`.
3. Use `Ctrl+P` to open the command palette.
4. Use `/settings` to review settings locations.

## Settings files

- User scope: `~/.villani/settings.json`
- Project scope: `.villani/settings.json`

## ASCII mockup

```text
🤖 Villani Code > Write tests for parser
net:connected/0s | tok:12 (12/m) | tools:0:- | settings:Ctrl+P
```


## Villani mode quick start

Run autonomous mode (no initial prompt required):

```bash
villani-code villani-mode --base-url http://127.0.0.1:1234 --model my-model
```

Villani mode scans the repository, ranks tasks, executes edits, verifies results, and prints a final structured summary with verification status.
