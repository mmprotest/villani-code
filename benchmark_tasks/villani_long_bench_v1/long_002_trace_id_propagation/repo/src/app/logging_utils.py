from __future__ import annotations
LOGS: list[str] = []

def log(message: str) -> None:
    LOGS.append(message)
