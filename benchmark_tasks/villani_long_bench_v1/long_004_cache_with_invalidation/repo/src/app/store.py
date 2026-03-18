from __future__ import annotations
DB = {'items': {}}

def put_item(item_id: str, value: int) -> None:
    DB['items'][item_id] = value

def get_item(item_id: str) -> int | None:
    return DB['items'].get(item_id)

def delete_item(item_id: str) -> None:
    DB['items'].pop(item_id, None)

def list_items() -> dict[str, int]:
    return dict(DB['items'])

def reset_store() -> None:
    DB['items'].clear()
