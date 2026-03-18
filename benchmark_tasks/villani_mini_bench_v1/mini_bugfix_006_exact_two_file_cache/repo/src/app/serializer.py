from __future__ import annotations
import json

def freeze_value(value: object) -> str:
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)
