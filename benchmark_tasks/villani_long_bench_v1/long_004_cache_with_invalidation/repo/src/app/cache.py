from __future__ import annotations
CACHE: dict[str, object] = {}
STATS = {'hits': 0, 'misses': 0}

def get(key: str):
    if key in CACHE:
        STATS['hits'] += 1
        return CACHE[key]
    STATS['misses'] += 1
    return None

def set_(key: str, value: object) -> None:
    CACHE[key] = value

def clear_prefix(prefix: str) -> None:
    for key in [k for k in CACHE if k.startswith(prefix)]:
        CACHE.pop(key, None)

def reset():
    CACHE.clear(); STATS['hits']=0; STATS['misses']=0
