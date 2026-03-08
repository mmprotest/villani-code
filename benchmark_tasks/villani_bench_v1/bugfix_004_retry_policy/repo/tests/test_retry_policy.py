from app.client import send_with_retry

def test_400_does_not_retry():
    assert send_with_retry([400, 200], max_retries=3)==1

def test_500_retries():
    assert send_with_retry([500, 500, 200], max_retries=3)==3

def test_429_retries_but_limited():
    assert send_with_retry([429, 429, 200], max_retries=1)==2
