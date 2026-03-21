import json


def format_summary(summary: dict[str, float], fmt: str) -> str:
    if fmt == 'json':
        return json.dumps(summary, sort_keys=True)
    return f"count={summary['count']} total={summary['total']} average={summary['average']}"
