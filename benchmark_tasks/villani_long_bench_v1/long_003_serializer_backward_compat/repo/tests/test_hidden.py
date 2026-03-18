from app.adapter import normalize
from app.models import Event
from app.serializer import dump_event, parse_event

def test_new_format_carries_source():
    payload = {'event': {'kind': 'click', 'amount': '2', 'meta': {'source': 'api'}}}
    assert normalize(payload) == {'kind': 'click', 'amount': 2, 'source': 'api'}

def test_roundtrip_new_format_preserves_semantics():
    event = Event(kind='view', amount=1, source='worker')
    dumped = dump_event(event, version='new')
    reparsed = parse_event(dumped)
    assert reparsed == event

def test_old_format_with_explicit_source_survives():
    event = parse_event({'type': 'sale', 'amount': 4, 'source': 'batch'})
    assert dump_event(event, version='old')['source'] == 'batch'
