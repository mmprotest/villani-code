from .commands import COMMANDS

def build_registry():
    registry = {}
    for name, spec in COMMANDS.items():
        registry[name] = name
        aliases = spec.get("aliases", [])
        if aliases:
            registry[aliases[0]] = name
    return registry
