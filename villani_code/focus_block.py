from __future__ import annotations

from villani_code.project_memory import SessionState


def _shorten(text: str, limit: int = 120) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _format_list(items: list[str], item_limit: int = 4, text_limit: int = 120) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        return ""
    shown = values[:item_limit]
    text = ", ".join(shown)
    if len(values) > item_limit:
        text += f", +{len(values) - item_limit} more"
    return _shorten(text, limit=text_limit)


def render_focus_block(state: SessionState, max_chars: int = 600) -> str:
    rows: list[tuple[str, str]] = []
    goal = state.current_goal or state.task_summary
    plan = _format_list(state.current_plan, item_limit=3, text_limit=140) or _shorten(
        state.plan_summary, limit=140
    )
    latest_error = state.latest_error or state.last_failed_step
    changed_files = _format_list(state.changed_files or state.affected_files, item_limit=4)
    failed = _format_list(state.failed_hypotheses, item_limit=3)

    for label, value in [
        ("Goal", _shorten(goal)),
        ("Plan", plan),
        ("Latest error", _shorten(latest_error)),
        ("Last command", _shorten(state.last_command)),
        ("Last result", _shorten(state.last_command_result)),
        ("Changed files", changed_files),
        ("Failed ideas", failed),
        ("Next action", _shorten(state.next_action)),
    ]:
        if value:
            rows.append((label, value))

    if not rows:
        return ""

    lines = ["[FOCUS]"]
    for label, value in rows:
        candidate = f"{label}: {value}"
        if sum(len(line) + 1 for line in lines) + len(candidate) + len("\n[/FOCUS]") > max_chars:
            break
        lines.append(candidate)
    lines.append("[/FOCUS]")
    return "\n".join(lines)
