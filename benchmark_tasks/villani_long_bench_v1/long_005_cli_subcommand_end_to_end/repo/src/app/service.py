from __future__ import annotations

def compute_stats(values: list[int]) -> dict:
    if not values:
        raise ValueError('no values')
    total = sum(values)
    return {'count': len(values), 'total': total, 'average': total / len(values)}
