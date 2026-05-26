from .core import build_lines

def render_report_v2(title: str, items: list[str], compact: bool = False) -> str:
    lines = build_lines(title, items)
    return ' | '.join(lines) if compact else '\n'.join(lines)
