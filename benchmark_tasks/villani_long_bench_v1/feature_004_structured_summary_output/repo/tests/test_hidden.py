from app.cli import main
from app.dashboard import build_widget_payload
from app.reporting import run_audit


def test_text_output_mentions_summary_without_changing_first_line():
    code, output = main([1, 2], fmt='text')
    assert code == 0
    assert output.splitlines()[0] == 'OK'
    assert output == 'OK\nsummary=checked=2 errors=0'



def test_failure_summary_matches_programmatic_and_widget_results():
    result = run_audit([1, -2, -3])
    assert result == {
        'ok': False,
        'errors': ['-2:must_be_non_negative', '-3:must_be_non_negative'],
        'summary': 'checked=3 errors=2',
    }
    assert build_widget_payload([1, -2, -3]) == {
        'ok': False,
        'error_count': 2,
        'summary': 'checked=3 errors=2',
    }
