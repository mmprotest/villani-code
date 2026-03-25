from app.http.retries import retry_delay_seconds

def test_retry_after_fractional_rounds_up_once():
    assert retry_delay_seconds("1.2") == 2

def test_retry_after_integral_stays_integral():
    assert retry_delay_seconds("2") == 2
