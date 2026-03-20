from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VILLANI_DIR = ".villani"
MAX_RECENT_ACTIONS = 12


@dataclass(slots=True)
class SessionMemory:
    current_goal: str = ""
    current_plan: list[str] = field(default_factory=list)
    attempted_fixes: list[str] = field(default_factory=list)
    failed_hypotheses: list[str] = field(default_factory=list)
    last_command: str = ""
    last_command_result: str = ""
    changed_files: list[str] = field(default_factory=list)
    latest_error: str = ""
    next_action: str = ""
    recent_actions: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionMemory":
        if not isinstance(payload, dict):
            return cls()

        def _text(key: str) -> str:
            value = payload.get(key, "")
            return value.strip() if isinstance(value, str) else ""

        def _text_list(key: str) -> list[str]:
            value = payload.get(key, [])
            if not isinstance(value, list):
                return []
            out: list[str] = []
            for item in value:
                text = item.strip() if isinstance(item, str) else ""
                if text:
                    out.append(text)
            return out

        return cls(
            current_goal=_text("current_goal"),
            current_plan=_text_list("current_plan"),
            attempted_fixes=_text_list("attempted_fixes"),
            failed_hypotheses=_text_list("failed_hypotheses"),
            last_command=_text("last_command"),
            last_command_result=_text("last_command_result"),
            changed_files=_text_list("changed_files"),
            latest_error=_text("latest_error"),
            next_action=_text("next_action"),
            recent_actions=_text_list("recent_actions"),
            updated_at=_text("updated_at"),
        )


def _dedupe_preserve(items: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    if limit is not None and len(ordered) > limit:
        return ordered[-limit:]
    return ordered


def session_state_path(repo: Path) -> Path:
    return repo / VILLANI_DIR / "session_state.json"


def load_session_state(repo: Path) -> SessionMemory:
    path = session_state_path(repo)
    if not path.exists():
        return SessionMemory()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return SessionMemory()
    return SessionMemory.from_dict(payload)


def merge_session_state(existing: SessionMemory, incoming: SessionMemory) -> SessionMemory:
    merged = SessionMemory.from_dict(existing.to_dict())
    for field_name in [
        "current_goal",
        "last_command",
        "last_command_result",
        "latest_error",
        "next_action",
    ]:
        value = getattr(incoming, field_name)
        if value.strip():
            setattr(merged, field_name, value.strip())
    if incoming.current_plan:
        merged.current_plan = _dedupe_preserve(incoming.current_plan, limit=6)
    merged.attempted_fixes = _dedupe_preserve(
        [*merged.attempted_fixes, *incoming.attempted_fixes],
        limit=12,
    )
    merged.failed_hypotheses = _dedupe_preserve(
        [*merged.failed_hypotheses, *incoming.failed_hypotheses],
        limit=12,
    )
    merged.changed_files = _dedupe_preserve(
        [*merged.changed_files, *incoming.changed_files],
        limit=24,
    )
    merged.recent_actions = _dedupe_preserve(
        [*merged.recent_actions, *incoming.recent_actions],
        limit=MAX_RECENT_ACTIONS,
    )
    merged.updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return merged


def save_session_state(repo: Path, state: SessionMemory) -> SessionMemory:
    path = session_state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = merge_session_state(SessionMemory(), state)
    path.write_text(json.dumps(normalized.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return normalized


def update_session_state(repo: Path, state: SessionMemory) -> SessionMemory:
    current = load_session_state(repo)
    merged = merge_session_state(current, state)
    return save_session_state(repo, merged)


def session_memory_from_project_state(state: Any) -> SessionMemory:
    task_summary = str(getattr(state, "task_summary", "") or "").strip()
    plan_summary = str(getattr(state, "plan_summary", "") or "").strip()
    changed_files = [str(item).strip() for item in getattr(state, "affected_files", []) if str(item).strip()]
    validation_summary = str(getattr(state, "validation_summary", "") or "").strip()
    last_failed_step = str(getattr(state, "last_failed_step", "") or "").strip()
    next_hints = [str(item).strip() for item in getattr(state, "next_step_hints", []) if str(item).strip()]
    return SessionMemory(
        current_goal=task_summary,
        current_plan=[plan_summary] if plan_summary else [],
        changed_files=changed_files,
        latest_error=last_failed_step or validation_summary,
        next_action=next_hints[0] if next_hints else "",
    )
