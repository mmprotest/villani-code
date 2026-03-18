from __future__ import annotations
from app.models import Event

def parse_event(payload: dict) -> Event:
    # old format: {'type': 'click', 'amount': 2}
    # new format: {'event': {'kind': 'click', 'amount': 2, 'meta': {'source': 'api'}}}
    if 'event' in payload:
        data = payload['event']
        return Event(kind=data['kind'], amount=int(data['amount']))  # BUG drops source
    return Event(kind=payload['type'], amount=int(payload['amount']), source=payload.get('source'))


def dump_event(event: Event, version: str = 'old') -> dict:
    if version == 'new':
        return {'event': {'kind': event.kind, 'amount': event.amount}}
    return {'type': event.kind, 'amount': event.amount, 'source': event.source}
