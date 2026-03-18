from __future__ import annotations

def run_validation(value: int) -> tuple[bool, list[str]]:
    errors = []
    if value < 0:
        errors.append('value must be >= 0')
    return not errors, errors
