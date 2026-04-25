from app.api import post_user

def test_legacy_name_payload_still_creates_user():
    result = post_user({'name': 'Ada Lovelace', 'email': 'ADA@EXAMPLE.COM'})
    assert result['user']['display_name'] == 'Ada Lovelace'
    assert result['user']['email'] == 'ada@example.com'
