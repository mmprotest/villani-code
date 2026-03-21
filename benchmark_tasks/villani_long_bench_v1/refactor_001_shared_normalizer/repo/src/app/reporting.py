def _normalize_record_name(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = cleaned.replace('-', ' ').replace('_', ' ')
    return ' '.join(cleaned.split())


def collect_report_labels(records: list[dict[str, str]]) -> list[str]:
    return [_normalize_record_name(record['name']) for record in records]
