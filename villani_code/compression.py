from __future__ import annotations

from typing import Any

from villani_code.turn_validation import extract_tool_use_ids, validate_tool_turns


def estimate_size(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {"text", "thinking"}:
                total += len(str(block.get("text", "") or block.get("thinking", "")))
            elif block_type == "tool_result":
                total += len(str(block.get("content", "")))
            elif block_type == "tool_use":
                total += len(str(block.get("input", {})))
    return total


def _recent_boundary(messages: list[dict[str, Any]], keep_recent_turns: int) -> int:
    keep_start = max(0, len(messages) - keep_recent_turns)
    if keep_start <= 0:
        return 0
    if keep_start >= len(messages):
        return len(messages)

    prev = messages[keep_start - 1] if keep_start - 1 >= 0 else None
    current = messages[keep_start]
    if prev and prev.get("role") == "assistant" and extract_tool_use_ids(prev):
        if current.get("role") == "user":
            return keep_start - 1
    return keep_start


def _summarize_assistant_block(block: dict[str, Any]) -> dict[str, Any] | None:
    block_type = block.get("type")
    if block_type == "tool_use":
        return block
    if block_type == "text":
        text = str(block.get("text", ""))
        return {"type": "text", "text": f"Summary of earlier assistant output: {text[:400]} ... [omitted]"}
    return None


def _summarize_user_block(block: dict[str, Any]) -> dict[str, Any] | None:
    block_type = block.get("type")
    if block_type == "tool_result":
        content = str(block.get("content", ""))
        return {
            "type": "tool_result",
            "tool_use_id": block.get("tool_use_id"),
            "is_error": block.get("is_error", False),
            "content": f"Tool result summarized: {content[:200]} ... [omitted]",
        }
    if block_type == "text":
        text = str(block.get("text", ""))
        return {"type": "text", "text": text[:300]}
    return None


def compress_messages(messages: list[dict[str, Any]], max_chars: int = 200000, keep_recent_turns: int = 10) -> list[dict[str, Any]]:
    if estimate_size(messages) <= max_chars:
        return messages

    keep_start = _recent_boundary(messages, keep_recent_turns)
    compressed: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if message.get("role") == "system" or index >= keep_start:
            compressed.append(message)
            continue

        role = message.get("role")
        blocks = [b for b in message.get("content", []) if isinstance(b, dict)]
        if role == "assistant":
            new_blocks = [out for block in blocks if (out := _summarize_assistant_block(block)) is not None]
            compressed.append({"role": "assistant", "content": new_blocks})
            continue

        if role == "user":
            new_blocks = [out for block in blocks if (out := _summarize_user_block(block)) is not None]
            compressed.append({"role": "user", "content": new_blocks})
            continue

        compressed.append(message)

    validate_tool_turns(compressed)
    return compressed
