from __future__ import annotations
from app import cache
from app.store import delete_item, get_item, list_items, put_item

def read_item(item_id: str):
    key = f'item:{item_id}'
    cached = cache.get(key)
    if cached is not None:
        return cached
    value = get_item(item_id)
    cache.set_(key, value)
    return value

def summary_total() -> int:
    key = 'summary:total'
    cached = cache.get(key)
    if cached is not None:
        return cached
    total = sum(list_items().values())
    cache.set_(key, total)
    return total

def create_or_update(item_id: str, value: int) -> None:
    put_item(item_id, value)
    cache.clear_prefix(f'item:{item_id}')
    # BUG summary cache not invalidated

def remove(item_id: str) -> None:
    delete_item(item_id)
    # BUG item + summary caches not invalidated
