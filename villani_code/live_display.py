from __future__ import annotations


def apply_live_display_delta(buffer: str, delta: str, started: bool) -> tuple[str, bool]:
    """Update the live-only assistant stream buffer without altering stored transcripts.

    Rules:
    - Ignore whitespace-only deltas until first non-whitespace content arrives.
    - Cap consecutive newlines at 2.
    """
    if not started and delta.strip() == "":
        return buffer, started

    started = started or bool(delta.strip())
    if not delta:
        return buffer, started

    updated = buffer
    for ch in delta:
        if ch != "\n":
            updated += ch
            continue
        trailing_newlines = len(updated) - len(updated.rstrip("\n"))
        if trailing_newlines < 2:
            updated += "\n"
    return updated, started
