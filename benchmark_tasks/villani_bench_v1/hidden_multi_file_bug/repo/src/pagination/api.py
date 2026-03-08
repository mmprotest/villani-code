from .iterator import page_items


def list_all_items(items: list[int], page_size: int) -> list[int]:
    cursor = 0
    merged: list[int] = []
    while True:
        page, cursor = page_items(items, cursor, page_size)
        merged.extend(page)
        if cursor is None:
            break
    return merged
