from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from villani_code.utils import normalize_content_blocks


PROPOSAL_TOOL_NAMES = {
    "propose_full_file_rewrite",
    "propose_snippet_replace",
    "propose_two_file_rewrite",
}


@dataclass(slots=True)
class ProposalToolCall:
    name: str
    arguments: dict[str, Any]
    tool_call_id: str = ""


@dataclass(slots=True)
class ProposalExtractionResult:
    call: ProposalToolCall | None
    raw_model_content: str
    raw_tool_calls: list[dict[str, Any]]
    raw_reasoning_content: str
    failure_reason: str = ""


def stage1_proposal_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "propose_full_file_rewrite",
            "description": "Propose a complete rewrite for one file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "new_content": {"type": "string"},
                    "rationale": {"type": ["string", "null"]},
                },
                "required": ["file_path", "new_content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "propose_snippet_replace",
            "description": "Propose one exact snippet replacement in one file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_snippet": {"type": "string"},
                    "new_snippet": {"type": "string"},
                    "rationale": {"type": ["string", "null"]},
                },
                "required": ["file_path", "old_snippet", "new_snippet"],
                "additionalProperties": False,
            },
        },
    ]


def stage2_proposal_tools(*, allow_adjacent_file_retry: bool = False) -> list[dict[str, Any]]:
    tools = stage1_proposal_tools()
    if allow_adjacent_file_retry:
        tools.append(
            {
                "name": "propose_two_file_rewrite",
                "description": "Propose complete rewrites for target file plus one explicitly allowed adjacent file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "primary_file_path": {"type": "string"},
                        "primary_new_content": {"type": "string"},
                        "secondary_file_path": {"type": "string"},
                        "secondary_new_content": {"type": "string"},
                        "rationale": {"type": ["string", "null"]},
                    },
                    "required": [
                        "primary_file_path",
                        "primary_new_content",
                        "secondary_file_path",
                        "secondary_new_content",
                    ],
                    "additionalProperties": False,
                },
            }
        )
    return tools


def extract_structured_proposal(response: dict[str, Any]) -> ProposalExtractionResult:
    content = normalize_content_blocks(response.get("content", []))
    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    reasoning_parts: list[str] = []

    for block in content:
        block_type = str(block.get("type", ""))
        if block_type in {"text", "output_text"}:
            fragment = str(block.get("text", block.get("content", ""))).strip()
            if fragment:
                text_parts.append(fragment)
        elif block_type == "tool_use":
            tool_uses.append(block)
        elif block_type in {"reasoning", "thinking", "reasoning_content"}:
            fragment = str(block.get("text", block.get("content", ""))).strip()
            if fragment:
                reasoning_parts.append(fragment)

    if not tool_uses:
        return ProposalExtractionResult(
            call=None,
            raw_model_content="\n".join(text_parts).strip(),
            raw_tool_calls=[],
            raw_reasoning_content="\n".join(reasoning_parts).strip(),
            failure_reason="no_tool_call_returned",
        )

    first = tool_uses[0]
    tool_name = str(first.get("name", "")).strip()
    raw_calls = [
        {
            "id": str(call.get("id", "")),
            "name": str(call.get("name", "")),
            "input": call.get("input", {}),
        }
        for call in tool_uses
    ]
    if tool_name not in PROPOSAL_TOOL_NAMES:
        return ProposalExtractionResult(
            call=None,
            raw_model_content="\n".join(text_parts).strip(),
            raw_tool_calls=raw_calls,
            raw_reasoning_content="\n".join(reasoning_parts).strip(),
            failure_reason="invalid_tool_name",
        )

    arguments = first.get("input", {})
    if not isinstance(arguments, dict):
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        else:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}

    return ProposalExtractionResult(
        call=ProposalToolCall(
            name=tool_name,
            arguments=arguments,
            tool_call_id=str(first.get("id", "")),
        ),
        raw_model_content="\n".join(text_parts).strip(),
        raw_tool_calls=raw_calls,
        raw_reasoning_content="\n".join(reasoning_parts).strip(),
    )
