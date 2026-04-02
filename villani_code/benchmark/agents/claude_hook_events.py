from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from villani_code.benchmark.adapters.base import AdapterEvent
from villani_code.benchmark.policy import normalize_path


@dataclass(frozen=True)
class HookParseResult:
    events: list[AdapterEvent]
    summary: dict[str, Any]


def parse_hook_events_jsonl(path: Path) -> HookParseResult:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return HookParseResult(events=[], summary={"exists": False, "records": 0, "errors": []})

    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {index}: {exc}")
            continue
        if isinstance(loaded, dict):
            records.append(loaded)

    events = [event for record in records for event in _record_to_events(record)]
    return HookParseResult(
        events=events,
        summary={
            "exists": True,
            "records": len(records),
            "events": len(events),
            "errors": errors,
        },
    )


def _record_to_events(record: dict[str, Any]) -> list[AdapterEvent]:
    hook_event = str(record.get("hook_event_name") or "").strip()
    tool_name = str(record.get("tool_name") or "").strip()
    tool_input = record.get("tool_input") if isinstance(record.get("tool_input"), dict) else {}
    ts = _timestamp(record)

    payload_base: dict[str, Any] = {
        "source": "claude_hook",
        "hook_event_name": hook_event,
        "tool_name": tool_name,
        "tool_use_id": record.get("tool_use_id"),
    }

    events: list[AdapterEvent] = []
    if tool_name == "Bash":
        command = tool_input.get("command") or tool_input.get("cmd")
        if isinstance(command, str):
            events.append(AdapterEvent(type="shell_command", timestamp=ts, payload={**payload_base, "type": "shell_command", "command": command[:500]}))
    if tool_name in {"Edit", "Write"}:
        path = _extract_path(tool_input)
        event_type = "file_edit" if tool_name == "Edit" else "write_file"
        payload = {**payload_base, "type": event_type}
        if path:
            payload["path"] = path
            payload["file_path"] = path
        events.append(AdapterEvent(type=event_type, timestamp=ts, payload=payload))
    if hook_event == "PostToolUseFailure" and tool_name in {"Bash", "Edit", "Write"}:
        events.append(
            AdapterEvent(
                type="command_failed",
                timestamp=ts,
                payload={
                    **payload_base,
                    "type": "command_failed",
                    "error": str(record.get("error") or "unknown")[:500],
                },
            )
        )
    if hook_event.lower() == "permissiondenied":
        events.append(
            AdapterEvent(
                type="benchmark_write_blocked",
                timestamp=ts,
                payload={
                    **payload_base,
                    "type": "benchmark_write_blocked",
                    "paths": [_extract_path(tool_input)] if _extract_path(tool_input) else [],
                    "reason": str(record.get("error") or "permission denied"),
                },
            )
        )

    return events


def _extract_path(tool_input: dict[str, Any]) -> str | None:
    for key in ("file_path", "path", "filename"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_path(value)
    return None


def _timestamp(record: dict[str, Any]) -> float:
    value = record.get("timestamp")
    if isinstance(value, (int, float)):
        return float(value)
    return time.monotonic()
