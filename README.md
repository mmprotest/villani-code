# Villani Code

```text
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                                                                              ┃
┃             ^                ^                ^                              ┃
┃            / \              / \              / \                             ┃
┃           /   \            /   \            /   \                            ┃
┃                                                                              ┃
┃   __     ___ _ _             _    ____          _                            ┃
┃   \ \   / (_) | | __ _ _ __ (_)  / ___|___   __| | ___                       ┃
┃    \ \ / /| | | |/ _` | '_ \| | | |   / _ \ / _` |/ _ \                      ┃
┃     \ V / | | | | (_| | | | | | | |__| (_) | (_| |  __/                      ┃
┃      \_/  |_|_|_|\__,_|_| |_|_|  \____\___/ \__,_|\___|                      ┃
┃                                                                              ┃
┃   Some tools help. Some tools assist. Villani Code intervenes.               ┃
┃                                                                              ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

**Villani Code** is an evil terminal coding agent.
It points at your repo, talks to a compatible model API, and plans plus executes tool calls (read, search, edit, bash, git, and more) to finish software tasks.

Repo status: thoroughly Villani.

Practical automation, Villani presentation.

## What it does

- Runs an agent loop against your repo.
- Streams model output in terminal or interactive TUI mode.
- Uses tool calls for file operations, shell commands, and git actions.
- Applies a permission/approval policy before sensitive actions.
- Stores checkpoints/transcripts under `.villani_code/` for traceability.

Lightweight agent, heavyweight Villani energy.

## Ministry of Villani

Official operating phrases for approved terminal overlords:

- Keep calm and dominate the workspace.
- Precision coding, theatrical menace.
- Local agent, global Villani agenda.
- Prompt first, ask questions never.
- Terminal infused with lawful evil.
- One more pass of strategic sabotage (of bad code).
- The terminal yearns for Villani.
- Operated by advanced evil engineering.
- Approved for controlled chaos.
- Warning: excessive Villani may improve throughput.

## Install

Install with a totally unreasonable amount of Villani:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Freshly Villani and medically inadvisable.

For development extras:

```bash
pip install -e .[dev]
```

Applied artisanal evil by hand.

## API compatibility

Villani Code supports two provider styles:

- `anthropic` (default): `POST {base_url}/v1/messages`
- `openai`: `POST {base_url}/v1/chat/completions`

Bringing order to chaos, then adding evil back.

Provider selection:

```bash
--provider anthropic|openai
```

API key lookup:

- `--api-key` (explicit, optional)
- if omitted and `--provider openai`: `OPENAI_API_KEY`
- if omitted and `--provider anthropic`: `ANTHROPIC_API_KEY`

## Core commands

Villani at the command line.

### 1) Default interactive mode (no subcommand)

When you run `villani-code` with no subcommand, interactive mode starts.

```bash
villani-code --base-url http://localhost:8000 --model local-model
```

Your repo has been approached by a caped coding menace.

### 2) Explicit interactive mode

```bash
villani-code interactive --base-url http://localhost:8000 --model local-model
```

Interactive mode, now with irresponsible amounts of evil.

### 3) One-shot run mode

```bash
villani-code run "summarize this repo" --base-url http://localhost:8000 --model local-model
```

One prompt in, one useful answer out, plus traces of theatrical panic.


### 4) Takeover mode (autonomous self-directed improvement)

Takeover mode is a first-class autonomous mode. It starts immediately without waiting for a user prompt, runs repo reconnaissance, ranks intervention opportunities, executes in bounded waves, adversarially verifies every wave, and stops when confidence/risk constraints require it.

```bash
villani-code takeover --base-url http://localhost:8000 --model local-model
```

You can also enable it from interactive entrypoints:

```bash
villani-code interactive --villani-mode --base-url http://localhost:8000 --model local-model
villani-code interactive --takeover --base-url http://localhost:8000 --model local-model
villani-code --villani-mode --base-url http://localhost:8000 --model local-model
```

Optional steering objective:

```bash
villani-code takeover "improve docs consistency" --base-url http://localhost:8000 --model local-model
```

Safety behavior in takeover mode:
- normal repo-local write/patch/test commands auto-resolve approval prompts
- hard destructive shell denylist remains active
- denied commands are recorded as blockers unless you explicitly pass `--unsafe`

New autonomy behaviors:
- **Adversarial self-verification:** after meaningful edits/validation commands, Villani runs a compact reviewer pass focused on regressions, incomplete edits, stale references/docs, side effects, and test gaps.
- **Failure-aware autonomy:** failures are classified (for example `test_failure`, `repo_ambiguity`, `verification_failure`, `repeated_no_progress`) and the next strategy changes based on class.
- **Confidence + risk summaries:** each wave reports confidence and risk level, plus why the intervention was chosen.

Takeover stop conditions:
- no remaining opportunities above confidence threshold
- blast radius exceeded configured wave constraints
- repeated failure patterns indicate no progress
- max wave limit reached

### Common options

- `--base-url` API server root URL
- `--model` model name
- `--repo` target repository path (default: `.`)
- `--max-tokens` max output tokens per model call
- `--small-model` enable constrained-model support mode
- `--provider anthropic|openai`
- `--api-key <token>`

Code review with traces of Villani.

## Typical workflow (end-to-end)

Now entering a high-Villani environment.

```text
1) Start Villani Code (interactive or run mode)
2) Submit a task
3) Agent builds context (system rules + repo state)
4) Agent requests model output
5) If tool calls are returned:
   - run permission policy (deny/ask/allow)
   - execute approved tools
   - append tool results
   - continue loop
6) If no more tool calls:
   - produce final response
   - write transcript/checkpoint artifacts
```

The clean room has been contaminated with delightful evil.

## Agent loop diagram

A flowchart of disciplined logic and questionable vibes:

```text
+-------------------+
| User prompt/input |
+---------+---------+
          |
          v
+---------------------------+
| Build run context         |
| - system/developer rules  |
| - repo + session state    |
+-------------+-------------+
              |
              v
+---------------------------+
| Plan next action          |
| (reasoning + constraints) |
+-------------+-------------+
              |
              v
+---------------------------+
| Need a tool call?         |
+--------+------------------+
         | yes                         no
         v                             v
+---------------------------+   +----------------------+
| Permission/safety check   |   | Draft direct answer  |
| (deny/ask/allow policies) |   | from current context |
+-------------+-------------+   +----------+-----------+
              |                            |
              v                            |
+---------------------------+              |
| Execute tool(s)           |              |
| (read/edit/bash/mcp/etc.) |              |
+-------------+-------------+              |
              |                            |
              v                            |
+---------------------------+              |
| Observe results           |<-------------+
| Update memory/checkpoints |
+-------------+-------------+
              |
              v
+---------------------------+
| Stop criteria met?        |
+--------+------------------+
         | no                         yes
         v                            v
   (loop back to plan)      +----------------------+
                             | Final response       |
                             | + transcript outputs |
                             +----------------------+
```

## Interactive mode notes

This is where the terminal theater actually happens:

- Inline approval prompts appear when policy requires confirmation.
- Streaming output is shown live.
- Scrolling/follow behavior is built into the TUI.

You drive. The agent improvises. The permission policy is the adult in the room.

Prompt first, ask questions never. (Menacingly.)

## Useful additional commands

Extensions, integrations, and controlled chaos:

```bash
villani-code mcp list
villani-code mcp add <name> <type> <endpoint>
villani-code mcp remove <name>
villani-code mcp reset-project-choices

villani-code plugin install <path>
villani-code plugin list
villani-code plugin remove <name>
```

Practical plugins, impractical Villani swagger.

## Help

For when you need less drama and more flags:

```bash
villani-code --help
villani-code run --help
villani-code interactive --help
```

Maximum Villani, minimum guesswork.
