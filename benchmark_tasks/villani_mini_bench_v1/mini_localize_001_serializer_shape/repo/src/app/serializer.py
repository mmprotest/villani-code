from __future__ import annotations
import json

def encode_payload(payload: dict[str, object]) -> str:
    clean = dict(payload)
    clean['tags'] = ','.join(clean.get('tags', []))
    return json.dumps(clean, sort_keys=True)
