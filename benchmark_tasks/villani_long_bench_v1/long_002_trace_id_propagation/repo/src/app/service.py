from __future__ import annotations
from app.errors import AppError
from app.logging_utils import log

def process(payload: dict, trace_id: str | None = None) -> dict:
    if payload.get('explode'):
        raise AppError('boom')
    nested = payload.get('nested', False)
    if nested:
        return forward({'value': payload['value']}, trace_id=None)  # BUG drops trace id
    log(f'trace={trace_id} processed value={payload.get("value")}')
    return {'ok': True, 'value': payload.get('value')}


def forward(payload: dict, trace_id: str | None = None) -> dict:
    log(f'trace={trace_id} forwarding')
    return process(payload, trace_id=trace_id)
