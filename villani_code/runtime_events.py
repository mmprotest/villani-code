from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EventKind = Literal[
    "status",
    "spinner",
    "stream_chunk",
    "tool_activity",
    "approval_requested",
    "approval_resolved",
    "validation",
    "benchmark_agent",
    "autonomous_stop",
]


@dataclass(slots=True)
class RuntimeEvent:
    kind: EventKind
    message: str
    durable: bool = True
    payload: dict[str, Any] | None = None

    @classmethod
    def from_runner_event(cls, event: dict[str, Any]) -> "RuntimeEvent | None":
        etype = str(event.get("type", ""))
        if etype in {"stream_text", "model_output_chunk"}:
            return cls(kind="stream_chunk", message=str(event.get("text", "")), durable=True, payload=event)
        if etype in {"tool_started", "tool_result", "tool_finished"}:
            tool = str(event.get("name", "tool"))
            return cls(kind="tool_activity", message=tool, durable=True, payload=event)
        if etype in {"approval_requested", "approval_auto_resolved", "approval_resolved"}:
            return cls(kind="approval_resolved" if "resolved" in etype else "approval_requested", message=etype, payload=event)
        if etype in {"validation_started", "validation_completed"}:
            return cls(kind="validation", message=etype, payload=event)
        if etype in {"villani_stop_decision", "autonomous_completed"}:
            return cls(kind="autonomous_stop", message=str(event.get("done_reason", etype)), payload=event)
        if etype:
            return cls(kind="status", message=etype, durable=False, payload=event)
        return None
