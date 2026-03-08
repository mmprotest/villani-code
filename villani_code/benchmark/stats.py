from __future__ import annotations

import random
from statistics import mean


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * ((p * (1 - p) + z * z / (4 * total)) / total) ** 0.5 / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def bootstrap_delta(a: list[int], b: list[int], n: int = 400) -> tuple[float, float, float]:
    if not a or not b:
        return 0.0, 0.0, 0.0
    deltas: list[float] = []
    for _ in range(n):
        sa = [random.choice(a) for _ in a]
        sb = [random.choice(b) for _ in b]
        deltas.append(mean(sa) - mean(sb))
    deltas.sort()
    lo = deltas[int(n * 0.025)]
    hi = deltas[int(n * 0.975)]
    return mean(a) - mean(b), lo, hi
