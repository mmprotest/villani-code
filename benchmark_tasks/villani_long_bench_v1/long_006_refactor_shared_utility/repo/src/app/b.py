from __future__ import annotations

def normalize_b(text: str) -> str:
    if not text:
        raise ValueError('empty')
    # BUG: preserves underscores and double separators incorrectly
    return text.strip().lower().replace(' ', '-')
