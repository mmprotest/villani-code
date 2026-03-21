from app.cli import main
from app.commands import run_stats
from app.formatters import format_summary


expected = 'count,total,average\n3,6,2.0'
assert run_stats([1, 2, 3], 'csv') == expected
assert main(['1', '2', '3', '--format', 'csv']) == (0, expected)
assert format_summary({'total': 6, 'average': 2.0, 'count': 3}, 'csv') == expected
