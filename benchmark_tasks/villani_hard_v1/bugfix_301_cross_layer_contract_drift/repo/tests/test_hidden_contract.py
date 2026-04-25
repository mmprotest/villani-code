import pytest
from app.api import post_user
from app.contracts import normalize_user_payload

def test_new_full_name_payload_still_works():
    assert post_user({'full_name': 'Grace Hopper', 'email': 'grace@example.com'})['user']['display_name'] == 'Grace Hopper'

def test_full_name_wins_when_both_keys_present():
    assert normalize_user_payload({'name': 'Wrong', 'full_name': 'Right', 'email': 'x@example.com'})['full_name'] == 'Right'

def test_blank_name_is_rejected_after_normalization():
    with pytest.raises(ValueError):
        normalize_user_payload({'name': '   ', 'email': 'x@example.com'})
