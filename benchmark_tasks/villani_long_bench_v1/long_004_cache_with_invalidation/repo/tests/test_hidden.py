from app import cache
from app.service import create_or_update, read_item, remove, summary_total
from app.store import reset_store

def setup_function():
    cache.reset(); reset_store()

def test_update_invalidates_summary():
    create_or_update('a', 3)
    assert summary_total() == 3
    create_or_update('a', 8)
    assert summary_total() == 8

def test_delete_invalidates_item_and_summary():
    create_or_update('a', 5)
    read_item('a')
    assert summary_total() == 5
    remove('a')
    assert read_item('a') is None
    assert summary_total() == 0

def test_multiple_items_use_normalized_summary_cache():
    create_or_update('a', 2)
    create_or_update('b', 4)
    assert summary_total() == 6
