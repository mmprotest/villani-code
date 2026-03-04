from __future__ import annotations

from pathlib import Path

from villani_code.utils import now_local_date


def build_system_blocks(repo: Path) -> list[dict[str, object]]:
    text = (
        "You are an interactive Villani Code agent for software engineering tasks. "
        "Use tools conservatively, verify changes, and keep outputs concise."
    )
    instructions = load_project_instructions(repo)
    blocks: list[dict[str, object]] = [{"type": "text", "text": text}]
    if instructions:
        blocks.append({"type": "text", "text": f"<project-instructions>\n{instructions}\n</project-instructions>"})
    blocks.append({"type": "text", "text": "Cache checkpoint.", "cache_control": {"type": "ephemeral"}})
    return blocks


def load_project_instructions(repo: Path) -> str:
    root = repo / "VILLANI.md"
    if not root.exists():
        return ""
    seen: set[Path] = set()

    def load_file(path: Path) -> str:
        if path in seen or not path.exists():
            return ""
        seen.add(path)
        content = path.read_text(encoding="utf-8")
        lines = []
        for line in content.splitlines():
            if line.startswith("@"):
                lines.append(load_file(repo / line[1:].strip()))
            else:
                lines.append(line)
        return "\n".join(lines)

    return load_file(root)


def build_initial_messages(repo: Path, user_instruction: str) -> list[dict[str, object]]:
    reminders = [
        "<system-reminder>Available tools in Villani Code include filesystem, search, shell, git, web fetch, and editing tools.</system-reminder>",
        f"<system-reminder>Current local date: {now_local_date()}. Repository root: {repo.resolve()}.</system-reminder>",
    ]
    reminder_blocks = [{"type": "text", "text": r} for r in reminders]
    reminder_blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return [{"role": "user", "content": reminder_blocks + [{"type": "text", "text": user_instruction}]}]
