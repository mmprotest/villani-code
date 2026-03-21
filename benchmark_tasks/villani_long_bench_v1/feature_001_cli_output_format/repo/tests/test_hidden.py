from app.cli import build_parser, main
from app.formatters import format_summary
from app.service import summarize_values


def test_csv_format_is_supported_programmatically():
    assert format_summary(summarize_values([2, 4]), 'csv') == 'count,total,average\n2,6,3.0'


def test_parser_help_lists_csv_choice():
    parser = build_parser()
    help_text = parser.format_help()
    assert 'csv' in help_text


def test_json_output_is_unchanged():
    code, output = main(['2', '4', '--format', 'json'])
    assert code == 0
    assert output == '{"average": 3.0, "count": 2, "total": 6}'
