def build_http_payload(result: dict[str, object]) -> dict[str, object]:
    warnings = list(result['warnings'])
    status = 'warn' if warnings else 'ok'
    return {
        'job': result['name'],
        'status': status,
        'warnings': warnings,
        'seconds': result['duration_seconds'],
    }
