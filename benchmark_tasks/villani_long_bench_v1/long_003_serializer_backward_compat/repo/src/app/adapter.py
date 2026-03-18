from __future__ import annotations
from app.serializer import parse_event

def normalize(payload: dict) -> dict:
    event = parse_event(payload)
    return {'kind': event.kind, 'amount': event.amount, 'source': event.source or 'unknown'}
