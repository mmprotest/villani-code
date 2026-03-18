from __future__ import annotations
from app.registry import load_registry

def available_commands() -> list[str]:
    return sorted(load_registry().keys())

def resolve_command(name: str) -> str | None:
    return load_registry().get(name)
