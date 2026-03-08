from app.csv_parser import parse_line

def test_quoted_comma():
    assert parse_line('a,"b,c",d')==['a','b,c','d']

def test_plain_commas():
    assert parse_line('a,b,d')==['a','b','d']

def test_escaped_quote():
    assert parse_line('"a""b",c')==['a"b','c']
