from pagination.api import list_all_items


def test_no_duplicates_when_iterating_pages() -> None:
    source = list(range(1, 8))
    merged = list_all_items(source, page_size=3)
    assert merged == source


def test_all_items_returned_when_last_page_is_partial() -> None:
    source = list(range(10, 17))
    merged = list_all_items(source, page_size=4)
    assert merged == source
