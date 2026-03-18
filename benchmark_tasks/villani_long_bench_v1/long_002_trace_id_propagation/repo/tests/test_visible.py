from app.api import handle_request
from app.logging_utils import LOGS

def setup_function():
    LOGS.clear()

def test_generates_and_logs_trace_id():
    status, body = handle_request({'value': 3})
    assert status == 200
    assert body['ok'] is True
    assert any('trace=' in line for line in LOGS)
