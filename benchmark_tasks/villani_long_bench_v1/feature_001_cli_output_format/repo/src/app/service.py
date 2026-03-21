def summarize_values(values: list[int]) -> dict[str, float]:
    total = sum(values)
    return {
        'count': len(values),
        'total': total,
        'average': total / len(values),
    }
