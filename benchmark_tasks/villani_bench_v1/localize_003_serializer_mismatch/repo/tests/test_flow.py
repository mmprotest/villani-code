from app.c import entry

def test_entry_float_preserved():
    assert entry(1.0) == '1.0'
