from app.store import Store

def test_write_then_read_sees_new_value():
    s = Store()
    s.put("a", 1)
    assert s.get("a") == 1
    s.put("a", 2)
    assert s.get("a") == 2

def test_other_keys_still_cache_normally():
    s = Store()
    s.put("a", 1)
    s.put("b", 3)
    assert s.get("b") == 3
    assert s.get("b") == 3
