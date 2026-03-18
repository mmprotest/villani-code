from app.api import handle_request
from app.logging_utils import LOGS

def setup_function():
    LOGS.clear()

def test_preserves_incoming_trace_id():
    status, _ = handle_request({'value': 9}, headers={'x-trace-id': 'abc-123'})
    assert status == 200
    assert any('trace=abc-123' in line for line in LOGS)

def test_error_payload_contains_trace_id():
    status, body = handle_request({'explode': True}, headers={'x-trace-id': 'err-7'})
    assert status == 500
    assert body == {'error': 'boom', 'trace_id': 'err-7'}

def test_nested_calls_keep_same_trace_id():
    status, _ = handle_request({'nested': True, 'value': 5}, headers={'x-trace-id': 'same-id'})
    assert status == 200
    assert all('same-id' in line for line in LOGS if 'trace=' in line)
