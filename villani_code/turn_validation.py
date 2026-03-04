from __future__ import annotations

from typing import Any


def _content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content", [])
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def extract_tool_use_ids(msg: dict[str, Any]) -> list[str]:
    return [str(block.get("id")) for block in _content_blocks(msg) if block.get("type") == "tool_use"]


def extract_tool_result_ids(msg: dict[str, Any]) -> list[str]:
    return [str(block.get("tool_use_id")) for block in _content_blocks(msg) if block.get("type") == "tool_result"]


def _tool_result_prefix_length(content: list[dict[str, Any]]) -> int:
    count = 0
    for block in content:
        if block.get("type") != "tool_result":
            break
        count += 1
    return count


def _is_tool_result_only_user_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    content = _content_blocks(message)
    return bool(content) and all(block.get("type") == "tool_result" for block in content)


def repair_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = _content_blocks(message)
        if repaired and role == "assistant" and repaired[-1].get("role") == "assistant":
            repaired[-1] = {"role": "assistant", "content": _content_blocks(repaired[-1]) + content}
            continue

        if repaired and _is_tool_result_only_user_message(message) and _is_tool_result_only_user_message(repaired[-1]):
            repaired[-1] = {"role": "user", "content": _content_blocks(repaired[-1]) + content}
            continue

        repaired.append({"role": role, "content": content})
    return repaired


def validate_tool_turns(messages: list[dict[str, Any]]) -> None:
    i = 0
    while i < len(messages):
        msg = messages[i]
        tool_use_ids = extract_tool_use_ids(msg)
        if msg.get("role") != "assistant" or not tool_use_ids:
            i += 1
            continue

        covered: set[str] = set()
        j = i + 1
        if j >= len(messages):
            raise ValueError(f"assistant message index {i} has tool_use id(s) {tool_use_ids} but no following user tool_result message")

        saw_result_block = False
        while j < len(messages):
            next_msg = messages[j]
            if next_msg.get("role") != "user":
                if covered:
                    break
                raise ValueError(
                    f"assistant message index {i} has tool_use id(s) {tool_use_ids} but next message index {j} role={next_msg.get('role')}"
                )

            content = _content_blocks(next_msg)
            prefix_len = _tool_result_prefix_length(content)
            if prefix_len == 0:
                if covered:
                    break
                raise ValueError(
                    f"assistant message index {i} has tool_use id(s) {tool_use_ids} but next user message index {j} does not start with tool_result blocks"
                )

            all_result_len = len([block for block in content if block.get("type") == "tool_result"])
            if prefix_len != all_result_len:
                raise ValueError(
                    f"user message index {j} has tool_result blocks that are not first in content"
                )

            saw_result_block = True
            covered.update(extract_tool_result_ids(next_msg))
            if set(tool_use_ids).issubset(covered):
                break
            j += 1

        missing = sorted(set(tool_use_ids) - covered)
        if missing:
            raise ValueError(
                f"assistant message index {i} tool_use id(s) {tool_use_ids} missing immediate tool_result for id(s) {missing}; chain stopped at message index {j}"
            )
        if not saw_result_block:
            raise ValueError(f"assistant message index {i} has tool_use id(s) {tool_use_ids} without matching tool_result blocks")

        i = j + 1
