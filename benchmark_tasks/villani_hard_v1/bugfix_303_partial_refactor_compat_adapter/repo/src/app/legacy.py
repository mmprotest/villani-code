from .adapters import render_legacy

def render_report(title: str, items: list[str], compact: bool = True) -> str:
    return render_legacy(title, items, compact=compact)
