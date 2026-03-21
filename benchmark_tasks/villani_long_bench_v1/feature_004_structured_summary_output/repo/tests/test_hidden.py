from app.cli import main
from app.reporting import run_audit


def test_text_output_mentions_summary():
    code, output = main([1, 2], fmt='text')
    assert code == 0
    assert output == 'OK\nsummary=checked=2 errors=0'


def test_failure_summary_matches_programmatic_result():
    result = run_audit([1, -2, -3])
    assert result == {
        'ok': False,
        'errors': ['-2:must_be_non_negative', '-3:must_be_non_negative'],
        'summary': 'checked=3 errors=2',
    }
