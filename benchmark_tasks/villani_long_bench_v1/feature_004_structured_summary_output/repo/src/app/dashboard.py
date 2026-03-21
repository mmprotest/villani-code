from .reporting import run_audit


def build_widget_payload(values: list[int]) -> dict[str, object]:
    result = run_audit(values)
    return {
        'ok': result['ok'],
        'error_count': len(result['errors']),
    }
