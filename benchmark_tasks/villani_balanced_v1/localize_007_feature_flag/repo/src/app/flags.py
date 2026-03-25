def is_enabled(flags: dict[str, bool], name: str) -> bool:
    return bool(flags.get(name, False))
