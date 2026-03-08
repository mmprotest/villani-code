from .flags import is_enabled

def render(flags: dict[str, bool]) -> str:
    if is_enabled(flags, "new- ui"):
        return "new"
    return "old"
