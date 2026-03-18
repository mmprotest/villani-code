from __future__ import annotations

def canonical_name(text: str) -> str:
    # BUG: utility exists but callers do not both use it, and current behavior is incomplete.
    return '-'.join(text.strip().lower().split())
