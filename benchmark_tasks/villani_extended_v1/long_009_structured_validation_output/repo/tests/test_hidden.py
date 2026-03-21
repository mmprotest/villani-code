import json
from app.cli import main

def test_json_success_output():
    code, text = main(1, fmt='json')
    assert code == 0
    payload = json.loads(text)
    assert payload == {'ok': True, 'errors': []}

def test_json_failure_output_and_exit_code():
    code, text = main(-1, fmt='json')
    assert code == 1
    payload = json.loads(text)
    assert payload == {'ok': False, 'errors': ['value must be >= 0']}
