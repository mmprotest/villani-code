from __future__ import annotations

import time
from pathlib import Path


def emit_runtime_event(repo: Path, event_callback, event_type: str, **payload: object) -> None:
    event = {"type": event_type, "event": event_type, "ts": time.time(), **payload}
    event_callback(event)
    events_path = repo / ".villani_code" / "runtime_events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(__import__("json").dumps(event))
        handle.write("\n")
