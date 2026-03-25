from app.serialize import serialize_user

def test_aliases_are_applied_without_dropping_other_fields():
    payload = serialize_user({'user_id': 7, 'email': 'a@example.com', 'created_at': '2024-01-01'})
    assert payload == {'userId': 7, 'email': 'a@example.com', 'createdAt': '2024-01-01'}

def test_unaliased_fields_are_preserved():
    payload = serialize_user({'email': 'a@example.com'})
    assert payload == {'email': 'a@example.com'}
