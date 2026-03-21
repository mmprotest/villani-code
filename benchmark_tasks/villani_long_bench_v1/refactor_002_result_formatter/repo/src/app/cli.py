def render_report(result: dict[str, object]) -> str:
    warnings = list(result['warnings'])
    status = 'warn' if warnings else 'ok'
    warning_text = ','.join(warnings) if warnings else '-'
    return f"job={result['name']} status={status} warnings={warning_text} seconds={result['duration_seconds']}"
