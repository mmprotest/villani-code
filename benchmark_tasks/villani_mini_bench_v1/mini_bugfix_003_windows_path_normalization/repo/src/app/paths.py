from __future__ import annotations

def normalize_path(value: str) -> str:
    value = value.replace(chr(92), '/')
    while '//' in value:
        value = value.replace('//', '/')
    if len(value) >= 2 and value[1] == ':':
        value = value[0].lower() + value[1:]
    return value.rstrip('/') or '/'
