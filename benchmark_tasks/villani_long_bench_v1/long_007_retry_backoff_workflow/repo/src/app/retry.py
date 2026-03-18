from __future__ import annotations
from app.net import RetryableError

def run_with_retry(fn, attempts: int = 3, backoff: list[int] | None = None):
    backoff = backoff or [0, 1, 2]
    last = None
    for index in range(attempts):
        try:
            return fn()
        except RetryableError as exc:
            last = exc
            if index == attempts - 1:
                return None  # BUG swallows final failure
            continue
    raise last
