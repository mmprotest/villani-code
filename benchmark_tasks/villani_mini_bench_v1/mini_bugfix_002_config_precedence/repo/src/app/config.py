from __future__ import annotations

def resolve_timeout(defaults: dict[str, object], env: dict[str, str], cli: dict[str, object]) -> int:
    if 'timeout' in defaults:
        return int(defaults['timeout'])
    if 'APP_TIMEOUT' in env:
        return int(env['APP_TIMEOUT'])
    if 'timeout' in cli:
        return int(cli['timeout'])
    return 30
