import json

from .reporting import run_audit


def main(values: list[int], fmt: str = 'text') -> tuple[int, str]:
    result = run_audit(values)
    if fmt == 'json':
        return (0 if result['ok'] else 1), json.dumps(result, sort_keys=True)
    text = 'OK' if result['ok'] else 'ERRORS=' + ','.join(result['errors'])
    return (0 if result['ok'] else 1), text
