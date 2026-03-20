from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from villani_code.project_memory import SessionState, VILLANI_DIR

MAX_RECENT_ACTIONS = 12


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


def load_session_state(repo: Path) -> SessionState:
    path = session_state_path(repo)
    if not path.exists():
        return SessionState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return SessionState()
    return SessionState.from_dict(payload)


def _merge_list(existing: list[str], incoming: list[str], *, limit: int | None = None) -> list[str]:
    return _dedupe_preserve([*existing, *incoming], limit=limit)


def merge_session_state(existing: SessionState, incoming: SessionState) -> SessionState:
    merged = SessionState.from_dict(existing.to_dict())

    replaceable_scalars = [
        "current_goal",
        "last_command",
        "last_command_result",
        "latest_error",
        "next_action",
        "task_summary",
        "plan_summary",
        "plan_risk",
        "estimated_scope",
        "change_impact",
        "task_mode",
        "validation_summary",
        "last_failed_step",
        "outcome_status",
        "handoff_checkpoint",
    ]
    for field_name in replaceable_scalars:
        value = getattr(incoming, field_name)
        if isinstance(value, str) and value.strip():
            setattr(merged, field_name, value.strip())

    merged.current_plan = incoming.current_plan or merged.current_plan
    merged.attempted_fixes = _merge_list(merged.attempted_fixes, incoming.attempted_fixes, limit=12)
    merged.failed_hypotheses = _merge_list(merged.failed_hypotheses, incoming.failed_hypotheses, limit=12)
    merged.changed_files = _merge_list(merged.changed_files, incoming.changed_files, limit=24)
    merged.recent_actions = _merge_list(merged.recent_actions, incoming.recent_actions, limit=MAX_RECENT_ACTIONS)
    merged.grounding_evidence_summary = _merge_list(merged.grounding_evidence_summary, incoming.grounding_evidence_summary, limit=8)
    merged.action_classes = _merge_list(merged.action_classes, incoming.action_classes, limit=8)
    merged.candidate_targets_summary = _merge_list(merged.candidate_targets_summary, incoming.candidate_targets_summary, limit=8)
    merged.affected_files = _merge_list(merged.affected_files, incoming.affected_files, limit=24)
    merged.validation_plan_summary = incoming.validation_plan_summary or merged.validation_plan_summary
    merged.next_step_hints = incoming.next_step_hints or merged.next_step_hints
    merged.repair_attempt_summaries = incoming.repair_attempt_summaries or merged.repair_attempt_summaries
    merged.updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return merged


def save_session_state(repo: Path, state: SessionState) -> SessionState:
    path = session_state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = merge_session_state(SessionState(), state)
    path.write_text(json.dumps(normalized.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return normalized


def update_session_state(repo: Path, state: SessionState) -> SessionState:
    current = load_session_state(repo)
    merged = merge_session_state(current, state)
    return save_session_state(repo, merged)
