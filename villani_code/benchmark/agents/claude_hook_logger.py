from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    output_path = Path(argv[1])
    raw = sys.stdin.read()
    record: dict[str, Any] = {
        "timestamp": time.time(),
        "raw": raw,
    }
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        record["parse_error"] = str(exc)
    else:
        if isinstance(payload, dict):
            record.update(
                {
                    "hook_event_name": payload.get("hook_event_name") or payload.get("event") or payload.get("hook_event"),
                    "tool_name": payload.get("tool_name") or payload.get("name"),
                    "tool_input": payload.get("tool_input") or payload.get("input"),
                    "tool_response": payload.get("tool_response") or payload.get("response"),
                    "error": payload.get("error"),
                    "tool_use_id": payload.get("tool_use_id") or payload.get("id"),
                    "cwd": payload.get("cwd"),
                    "session_id": payload.get("session_id"),
                    "payload": payload,
                }
            )
        else:
            record["payload"] = payload

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
