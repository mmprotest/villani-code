from app.api import build_wire_payload


def test_payload_preserves_list_shape():
    payload = build_wire_payload('demo', ['a', 'b'])['payload']
    assert '"tags": ["a", "b"]' in payload
