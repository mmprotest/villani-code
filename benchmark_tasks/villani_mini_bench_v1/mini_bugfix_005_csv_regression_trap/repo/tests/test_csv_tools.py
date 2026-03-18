from app.csv_tools import parse_csv_line


def test_simple_csv_line():
    assert parse_csv_line('alice, bob, carol') == ['alice', 'bob', 'carol']


def test_quoted_comma_is_kept_inside_field():
    assert parse_csv_line('alice,"bob, jr",carol') == ['alice', 'bob, jr', 'carol']


def test_escaped_quote_is_preserved():
    assert parse_csv_line('"say ""hi""",done') == ['say "hi"', 'done']
