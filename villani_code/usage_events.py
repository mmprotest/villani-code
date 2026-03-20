from __future__ import annotations

from typing import Any, Callable


def emit_model_usage_event(
    event_callback: Callable[[dict[str, Any]], None],
    response: dict[str, Any] | None,
    *,
    model: str | None,
    phase: str,
) -> None:
    if not isinstance(response, dict):
        return
    usage = response.get("usage")
    if not isinstance(usage, dict) or not usage:
        return
    event_callback(
        {
            "type": "model_usage",
            "model": model,
            "phase": phase,
            "usage": usage,
        }
    )
