from app.a import normalize_a
from app.b import normalize_b

def test_callers_match_on_edge_cases():
    for raw in ['Hello   World', 'Hello_World', '  Hello World  ']:
        assert normalize_a(raw) == normalize_b(raw)

def test_invalid_input_still_raises():
    try:
        normalize_a('   ')
    except ValueError:
        pass
    else:
        raise AssertionError('normalize_a should raise')
    try:
        normalize_b('')
    except ValueError:
        pass
    else:
        raise AssertionError('normalize_b should raise')
