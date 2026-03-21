from .formatters import format_summary
from .service import summarize_values


def run_stats(values: list[int], fmt: str = 'text') -> str:
    return format_summary(summarize_values(values), fmt)
