def build_notification(result: dict[str, object]) -> str:
    warnings = list(result['warnings'])
    status = 'warn' if warnings else 'ok'
    return f"notify[{status}] {result['name']} ({len(warnings)} warnings)"
