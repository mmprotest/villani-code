import json

from app.cli import main
from app.dashboard import build_widget_payload
from app.reporting import run_audit


def test_programmatic_result_includes_summary():
    assert run_audit([1, 2, 3]) == {
        'ok': True,
        'errors': [],
        'summary': 'checked=3 errors=0',
    }



def test_cli_json_and_widget_payload_include_summary():
    code, output = main([1, -2], fmt='json')
    assert code == 1
    assert json.loads(output) == {
        'errors': ['-2:must_be_non_negative'],
        'ok': False,
        'summary': 'checked=2 errors=1',
    }
    assert build_widget_payload([1, -2]) == {
        'ok': False,
        'error_count': 1,
        'summary': 'checked=2 errors=1',
    }
