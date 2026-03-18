from __future__ import annotations

def should_enable_feature(config: dict[str, object]) -> bool:
    raw = config.get('feature_enabled', False)
    if isinstance(raw, str):
        raw = raw.strip().lower() in {'1', 'true', 'yes', 'on'}
    return not bool(raw)
