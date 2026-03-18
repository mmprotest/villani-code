from app.adapter import normalize

def test_old_format_still_works():
    assert normalize({'type': 'click', 'amount': '2'}) == {'kind': 'click', 'amount': 2, 'source': 'unknown'}
