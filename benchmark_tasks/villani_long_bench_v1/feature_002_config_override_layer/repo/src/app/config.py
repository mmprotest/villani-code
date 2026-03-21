import json
import os
from pathlib import Path

from .defaults import DEFAULT_CONFIG


def _load_json(path: str | None) -> dict[str, object]:
    if not path:
        return {}
    payload = Path(path)
    if not payload.exists():
        return {}
    return json.loads(payload.read_text(encoding='utf-8'))


def load_config(config_path: str | None = None, env: dict[str, str] | None = None) -> dict[str, object]:
    values = dict(DEFAULT_CONFIG)
    values.update(_load_json(config_path))

    source = env or os.environ
    if 'APP_REGION' in source:
        values['region'] = source['APP_REGION']
    if 'APP_TIMEOUT' in source:
        values['timeout'] = int(source['APP_TIMEOUT'])
    if 'APP_RETRIES' in source:
        values['retries'] = int(source['APP_RETRIES'])
    return values
