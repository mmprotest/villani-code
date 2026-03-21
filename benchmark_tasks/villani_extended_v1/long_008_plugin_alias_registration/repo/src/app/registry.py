from __future__ import annotations
from app.plugins.sample import PLUGINS

def load_registry() -> dict[str, str]:
    registry = {}
    for plugin in PLUGINS:
        registry[plugin['name']] = plugin['handler']
        # BUG aliases are ignored and broken plugin crashes if naively accessed
    return registry
