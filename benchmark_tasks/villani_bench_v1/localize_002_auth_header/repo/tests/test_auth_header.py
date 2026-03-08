from app.client import request

def test_auth_header_propagates():
    assert request('abc')['Authorization']=='Bearer abc'

def test_trace_header_still_present():
    assert request(None)['X-Trace']=='1'
