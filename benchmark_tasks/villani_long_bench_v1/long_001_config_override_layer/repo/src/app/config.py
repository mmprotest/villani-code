from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = {"region": "us", "timeout": 10, "mode": "safe"}
_PROJECT_OVERRIDE_CACHE: dict[str, dict] = {}

@dataclass
class Config:
    region: str
    timeout: int
    mode: str


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _read_project_override(root: Path) -> dict:
    key = str(root.resolve())
    if key in _PROJECT_OVERRIDE_CACHE:
        return _PROJECT_OVERRIDE_CACHE[key]
    data = _read_json(root / 'project.local.json')
    _PROJECT_OVERRIDE_CACHE[key] = data
    return data


def clear_caches() -> None:
    pass


def load_config(root: str | Path, cli: dict | None = None, env: dict | None = None) -> Config:
    root = Path(root)
    cli = cli or {}
    env = env or os.environ
    base = dict(DEFAULTS)
    base.update(_read_json(root / 'config.json'))
    # BUG: project override should sit between env and config, not before config.
    base.update(_read_project_override(root))
    base.update({
        'region': env.get('APP_REGION', base.get('region')),
        'timeout': int(env.get('APP_TIMEOUT', base.get('timeout'))),
        'mode': env.get('APP_MODE', base.get('mode')),
    })
    base.update(cli)
    return Config(region=base['region'], timeout=int(base['timeout']), mode=base['mode'])
