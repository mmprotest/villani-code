from __future__ import annotations

def normalize_a(text: str) -> str:
    if not text.strip():
        raise ValueError('empty')
    return '-'.join(text.strip().lower().split())
