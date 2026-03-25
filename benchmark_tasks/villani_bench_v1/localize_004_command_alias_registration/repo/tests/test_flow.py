from app.c import entry

def test_remove_alias():
    assert entry('rm') == 'remove'
