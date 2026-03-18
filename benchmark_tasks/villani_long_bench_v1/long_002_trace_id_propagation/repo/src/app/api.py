from __future__ import annotations
import uuid
from app.errors import AppError
from app.logging_utils import log
from app.service import process

def handle_request(payload: dict, headers: dict | None = None) -> tuple[int, dict]:
    headers = headers or {}
    trace_id = headers.get('x-trace-id') or str(uuid.uuid4())
    try:
        body = process(payload, trace_id=trace_id)
        log(f'trace={trace_id} response=ok')
        return 200, body
    except AppError as exc:
        log(f'trace={trace_id} response=error')
        return 500, {'error': exc.message}  # BUG missing trace_id
