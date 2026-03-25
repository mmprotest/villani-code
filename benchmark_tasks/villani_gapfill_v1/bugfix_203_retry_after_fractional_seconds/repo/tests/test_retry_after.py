from app.retry import next_retry_seconds

def test_fractional_seconds_round_up_minimally():
    assert next_retry_seconds("0.2", default=0) == 1
    assert next_retry_seconds("1.2", default=0) == 2

def test_invalid_header_uses_default():
    assert next_retry_seconds("abc", default=3) == 3

def test_integral_value_kept():
    assert next_retry_seconds("2", default=0) == 2
