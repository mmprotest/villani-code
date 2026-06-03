from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from villani_code.runtime_events import RuntimeEvent
from villani_code.utils import ensure_dir


class RuntimeEventRecorder:
    def __init__(self, mission_dir: Path):
        self.mission_dir = mission_dir
        self.events_path = mission_dir / "runtime_events.jsonl"
        self.root_events_path = mission_dir.parent.parent / "runtime_events.jsonl" if mission_dir.parent.name == "missions" else mission_dir / "runtime_events.jsonl"
        self._events: list[dict[str, Any]] = []
        ensure_dir(mission_dir)
        ensure_dir(self.root_events_path.parent)

    def record(self, event: dict[str, Any]) -> None:
        mapped = RuntimeEvent.from_runner_event(event)
        phase = str(event.get("type", "status"))
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": phase,
            "phase": mapped.channel.value if mapped else "status",
            "durable": bool(mapped.durable) if mapped else True,
            "summary": str(mapped.message) if mapped else phase,
            "payload": event,
        }
        self._events.append(row)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        if self.root_events_path != self.events_path:
            with self.root_events_path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def build_digest(self) -> dict[str, Any]:
        grouped: Counter[str] = Counter()
        for row in self._events:
            etype = str(row.get("type", ""))
            if etype in {"Read", "tool_use", "tool_result", "tool_finished"}:
                grouped["tool_activity"] += 1
            elif etype.startswith("validation"):
                grouped["validations"] += 1
            elif etype.startswith("plan") or etype.startswith("planning"):
                grouped["planning"] += 1
            elif etype.startswith("autonomous") or etype.startswith("villani"):
                grouped["autonomous"] += 1
            elif "fail" in etype or "error" in etype:
                grouped["failures"] += 1
            else:
                grouped["status"] += 1
        return {
            "total_events": len(self._events),
            "groups": dict(grouped),
            "latest": self._events[-25:],
        }

    def write_digest(self) -> Path:
        path = self.mission_dir / "event_digest.json"
        path.write_text(json.dumps(self.build_digest(), indent=2), encoding="utf-8")
        return path
