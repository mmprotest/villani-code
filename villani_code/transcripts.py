from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from villani_code.utils import ensure_dir, now_stamp


def maybe_redact_payload(payload: dict[str, Any], redact: bool) -> dict[str, Any]:
    if not redact:
        return payload
    scrubbed = json.loads(json.dumps(payload))
    for msg in scrubbed.get("messages", []):
        for block in msg.get("content", []):
            if block.get("type") == "tool_result":
                block["content"] = "[REDACTED_TOOL_RESULT_CONTENT]"
    return scrubbed


def save_transcript(repo: Path, transcript: dict[str, Any], redact: bool = False) -> Path:
    out_dir = repo / ".villani_code" / "transcripts"
    ensure_dir(out_dir)
    path = out_dir / f"{now_stamp()}.json"
    to_write = dict(transcript)
    to_write.setdefault("schema_version", "2.0")
    to_write.setdefault("durable_events", [])
    if redact and "requests" in to_write:
        to_write["requests"] = [maybe_redact_payload(p, True) for p in to_write["requests"]]
    path.write_text(json.dumps(to_write, indent=2), encoding="utf-8")
    return path
