import json

from app.cli import main
from app.dashboard import build_widget_payload
from app.reporting import run_audit


assert run_audit([1, 2, 3]) == {
    'ok': True,
    'errors': [],
    'summary': 'checked=3 errors=0',
}
assert build_widget_payload([1, -2]) == {
    'ok': False,
    'error_count': 1,
    'summary': 'checked=2 errors=1',
}
code, output = main([1, -2], fmt='json')
assert code == 1
assert json.loads(output)['summary'] == 'checked=2 errors=1'
code, output = main([1, 2], fmt='text')
assert code == 0
assert output == 'OK\nsummary=checked=2 errors=0'
