from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ContextBudget:
    max_chars: int = 50_000
    keep_last_turns: int = 6

    def compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if _messages_chars(messages) <= self.max_chars:
            return messages

        system_messages = [m for m in messages if m.get("role") == "system"]
        others = [m for m in messages if m.get("role") != "system"]
        keep_count = self.keep_last_turns * 2
        tail = others[-keep_count:] if keep_count else []
        head = others[:-keep_count] if keep_count else others
        compacted_head = [self._compact_message(m) for m in head]
        compacted = [*system_messages, *compacted_head, *tail]

        while _messages_chars(compacted) > self.max_chars and compacted_head:
            compacted_head.pop(0)
            compacted = [*system_messages, *compacted_head, *tail]
        return compacted

    def _compact_message(self, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content")
        if not isinstance(content, list):
            return message
        compacted_blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                compacted_blocks.append(block)
                continue
            text = str(block.get("content", ""))
            if _preserve_exact(text):
                compacted_blocks.append(block)
                continue
            compacted_blocks.append({**block, "content": _summarize_tool_result(text)})
        return {**message, "content": compacted_blocks}


def _messages_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        total += len(str(content))
    return total


def _preserve_exact(text: str) -> bool:
    return any(marker in text for marker in ("@@", "--- ", "+++ ", "diff --git"))


def _summarize_tool_result(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    cmd = next((ln for ln in lines if "command" in ln.lower()), "")
    exit_line = next((ln for ln in lines if "exit" in ln.lower()), "")
    file_lines = [ln for ln in lines if any(tag in ln for tag in (".py", ".ts", ".js", ".md", "file_path"))][:2]
    err_lines = [ln for ln in lines if any(tok in ln.lower() for tok in ("error", "traceback", "failed"))][:3]
    summary = ["[compacted tool output]"]
    summary.extend([ln for ln in [cmd, exit_line] if ln])
    summary.extend(file_lines)
    summary.extend(err_lines)
    if len(summary) == 1:
        summary.append(lines[0][:200])
    return "\n".join(summary)
