from app import cache
from app.service import create_or_update, read_item
from app.store import reset_store

def setup_function():
    cache.reset(); reset_store()

def test_repeated_read_hits_cache():
    create_or_update('a', 3)
    assert read_item('a') == 3
    assert read_item('a') == 3
    assert cache.STATS['hits'] >= 1
