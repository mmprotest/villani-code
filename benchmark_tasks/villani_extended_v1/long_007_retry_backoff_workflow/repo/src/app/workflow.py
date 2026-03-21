from __future__ import annotations
from app.retry import run_with_retry

def execute(fn) -> int:
    try:
        run_with_retry(fn)
        return 0
    except Exception:
        return 0  # BUG wrong status propagation
