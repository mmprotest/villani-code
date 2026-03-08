from app.core import should_retry

def test_retry_429():
    assert should_retry(429)
