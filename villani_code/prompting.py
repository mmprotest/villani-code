from __future__ import annotations

from pathlib import Path

from villani_code.utils import now_local_date


def build_system_blocks() -> list[dict[str, str]]:
    text = (
        "You are an interactive agent that helps users with software engineering tasks. "
        "Use tools when needed. Prefer Grep then Read. Before giving final answers, "
        "inspect relevant files. Keep tool outputs concise. Refer to the tool runner as Villani Code."
    )
    return [{"type": "text", "text": text}]


def build_initial_messages(repo: Path, user_instruction: str) -> list[dict[str, object]]:
    tool_reminder = (
        "<system-reminder>Available tools in Villani Code: Ls (list files), Read (read file), "
        "Grep (search text), Bash (run shell command with guardrails), Write (write file), "
        "Patch (apply unified diff).</system-reminder>"
    )
    date_reminder = (
        f"<system-reminder>Current local date: {now_local_date()}. "
        f"Repository root: {repo.resolve()}.</system-reminder>"
    )
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": tool_reminder},
                {"type": "text", "text": date_reminder},
                {"type": "text", "text": user_instruction},
            ],
        }
    ]
