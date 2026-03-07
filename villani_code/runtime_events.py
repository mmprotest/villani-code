from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RuntimeEventChannel(StrEnum):
    TRANSCRIPT = "transcript"
    STATUS = "status"
    APPROVAL = "approval"
    BENCHMARK = "benchmark"
    AUTONOMOUS = "autonomous"


class RuntimeEventType(StrEnum):
    STREAM_CHUNK = "stream_chunk"
    TOOL_ACTIVITY = "tool_activity"
    STATUS = "status"
    SPINNER = "spinner"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    VALIDATION = "validation"
    BENCHMARK_LIFECYCLE = "benchmark_lifecycle"
    AUTONOMOUS_COMPLETION = "autonomous_completion"


@dataclass(slots=True)
class RuntimeEvent:
    event_type: RuntimeEventType
    channel: RuntimeEventChannel
    message: str
    durable: bool = True
    payload: dict[str, Any] | None = None

    @classmethod
    def from_runner_event(cls, event: dict[str, Any]) -> "RuntimeEvent | None":
        etype = str(event.get("type", ""))

        if etype in {"stream_text", "model_output_chunk"}:
            return cls(
                event_type=RuntimeEventType.STREAM_CHUNK,
                channel=RuntimeEventChannel.TRANSCRIPT,
                message=str(event.get("text", "")),
                durable=True,
                payload=event,
            )

        if etype in {"tool_started", "tool_result", "tool_finished"}:
            return cls(
                event_type=RuntimeEventType.TOOL_ACTIVITY,
                channel=RuntimeEventChannel.TRANSCRIPT,
                message=str(event.get("name", "tool")),
                durable=True,
                payload=event,
            )

        if etype in {"approval_requested"}:
            return cls(
                event_type=RuntimeEventType.APPROVAL_REQUESTED,
                channel=RuntimeEventChannel.APPROVAL,
                message=etype,
                durable=False,
                payload=event,
            )

        if etype in {"approval_auto_resolved", "approval_resolved"}:
            return cls(
                event_type=RuntimeEventType.APPROVAL_RESOLVED,
                channel=RuntimeEventChannel.APPROVAL,
                message=etype,
                durable=True,
                payload=event,
            )

        if etype in {"validation_started", "validation_completed"}:
            return cls(
                event_type=RuntimeEventType.VALIDATION,
                channel=RuntimeEventChannel.TRANSCRIPT,
                message=etype,
                durable=True,
                payload=event,
            )

        if etype.startswith("benchmark_"):
            return cls(
                event_type=RuntimeEventType.BENCHMARK_LIFECYCLE,
                channel=RuntimeEventChannel.BENCHMARK,
                message=etype,
                durable=True,
                payload=event,
            )

        if etype in {"villani_stop_decision", "autonomous_completed"}:
            return cls(
                event_type=RuntimeEventType.AUTONOMOUS_COMPLETION,
                channel=RuntimeEventChannel.AUTONOMOUS,
                message=str(event.get("done_reason", etype)),
                durable=True,
                payload=event,
            )

        if etype in {
            "planning_started",
            "plan_approved",
            "repair_attempt_started",
            "model_request_started",
            "first_text_delta",
            "autonomous_phase",
        }:
            mapped = RuntimeEventType.SPINNER if etype in {"model_request_started", "first_text_delta"} else RuntimeEventType.STATUS
            return cls(
                event_type=mapped,
                channel=RuntimeEventChannel.STATUS,
                message=etype,
                durable=False,
                payload=event,
            )

        if etype:
            return cls(
                event_type=RuntimeEventType.STATUS,
                channel=RuntimeEventChannel.STATUS,
                message=etype,
                durable=False,
                payload=event,
            )
        return None
