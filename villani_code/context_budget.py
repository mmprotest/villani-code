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

    def compact_session_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compact long chat sessions while preserving tool sequencing validity.

        Keeps full fidelity for recent turns and replaces older turns with one synthetic
        summary message that captures task continuity.
        """
        if _messages_chars(messages) <= self.max_chars:
            return messages

        system_messages = [m for m in messages if m.get("role") == "system"]
        others = [m for m in messages if m.get("role") != "system"]
        units = _group_atomic_units(others)
        keep_unit_count = max(1, self.keep_last_turns)
        head_units = units[:-keep_unit_count]
        tail_units = units[-keep_unit_count:]

        if not head_units:
            return messages

        head_messages = [message for unit in head_units for message in unit]
        tail_messages = [message for unit in tail_units for message in unit]
        summary = _build_session_summary(head_messages)
        summary_message = {
            "role": "user",
            "content": [{"type": "text", "text": summary}],
        }
        compacted = [*system_messages, summary_message, *tail_messages]

        while _messages_chars(compacted) > self.max_chars and len(tail_units) > 1:
            tail_units = tail_units[1:]
            tail_messages = [message for unit in tail_units for message in unit]
            compacted = [*system_messages, summary_message, *tail_messages]
        return compacted


def _group_atomic_units(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    units: list[list[dict[str, Any]]] = []
    idx = 0
    while idx < len(messages):
        current = messages[idx]
        if _is_tool_use_message(current) and idx + 1 < len(messages) and _is_tool_result_message(messages[idx + 1]):
            units.append([current, messages[idx + 1]])
            idx += 2
            continue
        units.append([current])
        idx += 1
    return units


def _is_tool_use_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "assistant":
        return False
    content = message.get("content", [])
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_use" for block in content
    )


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content", [])
    return isinstance(content, list) and bool(content) and all(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def _iter_text_blocks(messages: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text", "")).strip()
                if text:
                    lines.append(text)
    return lines


def _build_session_summary(messages: list[dict[str, Any]]) -> str:
    text_blocks = _iter_text_blocks(messages)
    objectives = [line for line in text_blocks if line.startswith("> ")][:3]
    if not objectives:
        objectives = text_blocks[:2]

    files_read: list[str] = []
    edits: list[str] = []
    validations: list[str] = []
    blockers: list[str] = []
    for message in messages:
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name", "")).strip()
                payload = block.get("input", {}) if isinstance(block.get("input"), dict) else {}
                path = str(payload.get("file_path", "")).strip()
                command = str(payload.get("command", "")).strip()
                if name == "Read" and path:
                    files_read.append(path)
                elif name in {"Write", "Patch", "Edit"} and path:
                    edits.append(f"{name} {path}")
                elif name == "Bash" and command:
                    validations.append(command)
            if block.get("type") == "text":
                line = str(block.get("text", "")).strip()
                low = line.lower()
                if any(token in low for token in ("error", "failed", "block", "todo", "unresolved", "question")):
                    blockers.append(line)

    def _uniq(items: list[str], limit: int = 6) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    objectives = _uniq(objectives, 4)
    files_read = _uniq(files_read, 8)
    edits = _uniq(edits, 8)
    validations = _uniq(validations, 8)
    blockers = _uniq(blockers, 5)

    lines = [
        "[session summary: compacted earlier turns]",
        "Prior user objectives:",
        *(f"- {item}" for item in (objectives or ["(none captured)"])),
        "Files read / inspected:",
        *(f"- {item}" for item in (files_read or ["(none captured)"])),
        "Edits performed:",
        *(f"- {item}" for item in (edits or ["(none captured)"])),
        "Validation / verification outcomes:",
        *(f"- {item}" for item in (validations or ["(none captured)"])),
        "Current blockers or unresolved questions:",
        *(f"- {item}" for item in (blockers or ["(none captured)"])),
    ]
    return "\n".join(lines)


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
