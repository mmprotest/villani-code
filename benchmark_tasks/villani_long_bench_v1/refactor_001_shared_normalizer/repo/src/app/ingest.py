def _normalize_record_name(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = cleaned.replace('-', ' ').replace('_', ' ')
    return ' '.join(cleaned.split())


def prepare_user_record(record: dict[str, str]) -> dict[str, str]:
    name = _normalize_record_name(record['name'])
    return {'name': name, 'slug': name.replace(' ', '-')}
