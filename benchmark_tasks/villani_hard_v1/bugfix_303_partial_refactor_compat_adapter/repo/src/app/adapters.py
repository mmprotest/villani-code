from .new_api import render_report_v2

def render_legacy(title: str, items: list[str], compact: bool = True) -> str:
    return render_report_v2(title, items, compact=compact)
