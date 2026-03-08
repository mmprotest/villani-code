from app.core import normalize_path, should_retry, paginate

def test_retry():
    assert should_retry(500)
    assert not should_retry(400)

def test_path():
    assert normalize_path('a\\b') == 'a/b'

def test_paginate():
    pages = paginate([1,2,3,4], 3)
    assert pages[-1] == [4]
