from .config import AUTOESCAPE
from .filters import apply_filters, safe_escape

def render(text: str, autoescape: bool | None = None) -> str:
    value = apply_filters(text)
    enabled = AUTOESCAPE if autoescape is None else autoescape
    if AUTOESCAPE:
        return safe_escape(value)
    return value
