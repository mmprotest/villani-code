from __future__ import annotations

import json
from typing import Any, Iterable


def parse_sse_events(response_stream: Iterable[str | bytes]):
    for raw_line in response_stream:
        if raw_line is None:
            continue
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="ignore")
        else:
            line = raw_line
        line = line.strip()
        if not line:
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
        if not line or line == "[DONE]":
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def assemble_anthropic_stream(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    response: dict[str, Any] = {"content": []}
    partial_json: dict[int, str] = {}

    for event in events:
        etype = event.get("type")
        if etype == "message_start":
            msg = event.get("message", {})
            response = dict(msg)
            response["content"] = []
        elif etype == "content_block_start":
            index = event.get("index", 0)
            block = dict(event.get("content_block", {}))
            while len(response["content"]) <= index:
                response["content"].append({})
            response["content"][index] = block
        elif etype == "content_block_delta":
            index = event.get("index", 0)
            delta = event.get("delta", {})
            while len(response["content"]) <= index:
                response["content"].append({})
            block = response["content"][index]
            dtype = delta.get("type")
            if dtype == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
            elif dtype == "text_delta":
                block["text"] = block.get("text", "") + delta.get("text", "")
            elif "partial_json" in dtype if isinstance(dtype, str) else False:
                partial_json[index] = partial_json.get(index, "") + delta.get("partial_json", "")
            elif dtype == "input_json_delta":
                partial_json[index] = partial_json.get(index, "") + delta.get("partial_json", "")
        elif etype == "content_block_stop":
            index = event.get("index", 0)
            if index in partial_json:
                raw = partial_json.pop(index)
                try:
                    parsed = json.loads(raw)
                    response["content"][index]["input"] = parsed
                except json.JSONDecodeError:
                    response["content"][index]["input"] = raw
        elif etype == "message_delta":
            delta = event.get("delta", {})
            if isinstance(delta, dict):
                response.update(delta)
            if "usage" in event:
                response["usage"] = event["usage"]
        elif etype == "message_stop":
            break

    return response
