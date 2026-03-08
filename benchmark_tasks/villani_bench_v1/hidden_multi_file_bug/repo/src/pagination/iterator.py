from .cursor import next_cursor


def page_items(items: list[int], cursor: int, page_size: int) -> tuple[list[int], int | None]:
    page = items[cursor : cursor + page_size]
    if not page:
        return [], None
    nxt = next_cursor(cursor, page_size)
    if nxt >= len(items):
        return page, None
    return page, nxt
