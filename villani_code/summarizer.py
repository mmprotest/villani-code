from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from villani_code.mission_state import MissionState


def summarize_tool_batch(events: Iterable[dict[str, Any]]) -> str:
    names: list[str] = []
    failures = 0
    for event in events:
        name = str(event.get("name", "")).strip()
        if name:
            names.append(name)
        if bool(event.get("is_error", False)):
            failures += 1
    if not names:
        return "No tool activity recorded."
    unique = list(dict.fromkeys(names))
    return f"Tools used: {', '.join(unique[:8])}. Failures: {failures}."


def summarize_validation(result: Any) -> str:
    if result is None:
        return "Validation not run."
    passed = bool(getattr(result, "passed", False))
    if passed:
        return "Validation passed."
    summary = str(getattr(result, "failure_summary", "")).strip()
    return f"Validation failed: {summary}" if summary else "Validation failed."


def summarize_patch(files_changed: list[str], diff_stats: dict[str, Any] | None = None) -> str:
    count = len(files_changed)
    if count == 0:
        return "No files changed."
    stats = diff_stats or {}
    ins = int(stats.get("insertions", 0) or 0)
    dele = int(stats.get("deletions", 0) or 0)
    return f"Patched {count} files ({ins} insertions, {dele} deletions)."


def summarize_mission_state(mission_state: MissionState) -> str:
    lines = [
        f"Mission {mission_state.mission_id}: {mission_state.objective}",
        f"Mode={mission_state.mode}; status={mission_state.status}",
    ]
    if mission_state.changed_files:
        lines.append(f"Changed files: {', '.join(mission_state.changed_files[:6])}")
    if mission_state.validation_failures:
        lines.append(f"Validation failures: {mission_state.validation_failures[0]}")
    if mission_state.autonomous_stop_reason:
        lines.append(f"Autonomous stop: {mission_state.autonomous_stop_reason}")
    return "\n".join(lines)
