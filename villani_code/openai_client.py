from __future__ import annotations

import json
from typing import Any, Generator, Iterable

import httpx


def normalize_openai_base_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


def convert_tools_to_openai(anthropic_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        }
        for tool in anthropic_tools
    ]


def _join_text_blocks(blocks: list[dict[str, Any]]) -> str:
    return "\n\n".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")


def convert_messages_to_openai(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system_blocks = payload.get("system", [])
    if system_blocks:
        out.append({"role": "system", "content": _join_text_blocks(system_blocks)})

    for message in payload.get("messages", []):
        role = message.get("role")
        blocks = message.get("content", [])
        text_content = _join_text_blocks(blocks)
        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]

        if role in {"user", "assistant"} and (text_content or (role == "assistant" and tool_uses)):
            converted: dict[str, Any] = {"role": role, "content": text_content}
            if role == "assistant" and tool_uses:
                converted["tool_calls"] = [
                    {
                        "id": str(tool_use.get("id", "")),
                        "type": "function",
                        "function": {
                            "name": str(tool_use.get("name", "")),
                            "arguments": json.dumps(tool_use.get("input", {})),
                        },
                    }
                    for tool_use in tool_uses
                ]
            out.append(converted)

        if role == "user" and tool_results:
            for tool_result in tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(tool_result.get("tool_use_id", "")),
                        "content": str(tool_result.get("content", "")),
                    }
                )
    return out


def build_openai_payload(payload: dict[str, Any], stream: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": convert_messages_to_openai(payload),
        "max_tokens": payload.get("max_tokens"),
        "stream": stream,
    }
    if payload.get("tools"):
        out["tools"] = convert_tools_to_openai(payload["tools"])
    if stream:
        out["stream_options"] = {"include_usage": True}
    return out


def _map_openai_finish_reason_to_anthropic(finish_reason: str | None) -> str | None:
    mapping = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "stop",
    }
    if finish_reason is None:
        return None
    return mapping.get(finish_reason, finish_reason)


def openai_stream_to_anthropic_events(lines: Iterable[str | bytes], model: str) -> Generator[dict[str, Any], None, None]:
    yield {"type": "message_start", "message": {"id": "openai", "type": "message", "role": "assistant", "model": model, "content": []}}
    text_started = False
    tool_indices: dict[int, int] = {}
    last_usage: dict[str, Any] | None = None
    last_finish_reason: str | None = None

    for raw in lines:
        line = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data_str = line[len("data:") :].strip()
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        usage = data.get("usage")
        if isinstance(usage, dict):
            last_usage = usage
        choices = data.get("choices", [])
        if not choices:
            continue
        finish_reason = choices[0].get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            last_finish_reason = finish_reason
        delta = choices[0].get("delta", {})
        text_delta = delta.get("content")
        if isinstance(text_delta, str) and text_delta:
            if not text_started:
                text_started = True
                yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
            yield {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text_delta}}

        for tool_call in delta.get("tool_calls", []):
            call_idx = int(tool_call.get("index", 0))
            if call_idx not in tool_indices:
                block_index = len(tool_indices) + 1
                tool_indices[call_idx] = block_index
                function = tool_call.get("function", {})
                yield {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": str(tool_call.get("id", "")),
                        "name": str(function.get("name", "")),
                        "input": {},
                    },
                }
            fragment = tool_call.get("function", {}).get("arguments")
            if isinstance(fragment, str) and fragment:
                yield {
                    "type": "content_block_delta",
                    "index": tool_indices[call_idx],
                    "delta": {"type": "input_json_delta", "partial_json": fragment},
                }

    if text_started:
        yield {"type": "content_block_stop", "index": 0}
    for block_index in tool_indices.values():
        yield {"type": "content_block_stop", "index": block_index}
    message_stop: dict[str, Any] = {"type": "message_stop"}
    if last_usage is not None:
        message_stop["usage"] = last_usage
    mapped_stop_reason = _map_openai_finish_reason_to_anthropic(last_finish_reason)
    if mapped_stop_reason is not None:
        message_stop["stop_reason"] = mapped_stop_reason
    yield message_stop


def convert_openai_response_to_anthropic(response: dict[str, Any]) -> dict[str, Any]:
    message = ((response.get("choices") or [{}])[0]).get("message", {})
    finish_reason = ((response.get("choices") or [{}])[0]).get("finish_reason")
    content: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    for tool_call in message.get("tool_calls", []) or []:
        function = tool_call.get("function", {})
        arguments = function.get("arguments", "{}")
        try:
            parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed_arguments = {}
        content.append(
            {
                "type": "tool_use",
                "id": str(tool_call.get("id", "")),
                "name": str(function.get("name", "")),
                "input": parsed_arguments,
            }
        )
    out: dict[str, Any] = {
        "id": response.get("id"),
        "type": "message",
        "role": str(message.get("role", "assistant")),
        "model": response.get("model"),
        "content": content,
    }
    usage = response.get("usage")
    if isinstance(usage, dict):
        out["usage"] = usage
    stop_reason = _map_openai_finish_reason_to_anthropic(finish_reason if isinstance(finish_reason, str) else None)
    if stop_reason is not None:
        out["stop_reason"] = stop_reason
    return out


class OpenAIClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 120.0):
        self.base_url = normalize_openai_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def create_message(self, payload: dict[str, Any], stream: bool) -> dict[str, Any] | Generator[dict[str, Any], None, None]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        request_payload = build_openai_payload(payload, stream)

        if not stream:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=request_payload, headers=headers)
                response.raise_for_status()
                return convert_openai_response_to_anthropic(response.json())

        def gen() -> Generator[dict[str, Any], None, None]:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", url, json=request_payload, headers=headers) as response:
                    response.raise_for_status()
                    yield from openai_stream_to_anthropic_events(response.iter_lines(), str(payload.get("model", "")))

        return gen()
