from __future__ import annotations
from .serializer import encode_payload

def build_wire_payload(name: str, tags: list[str]) -> dict[str, str]:
    return {'payload': encode_payload({'name': name, 'tags': tags})}
