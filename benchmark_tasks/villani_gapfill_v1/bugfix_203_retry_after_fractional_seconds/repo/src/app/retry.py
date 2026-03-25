import math

def next_retry_seconds(header_value: str | None, default: int = 1) -> int:
    if not header_value:
        return default
    try:
        value = float(header_value)
    except ValueError:
        return default
    return max(default, math.ceil(value + 1))
