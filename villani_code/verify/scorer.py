from __future__ import annotations


def score_patch(
    *,
    target_verification: float,
    collateral_verification: float,
    static_sanity: float,
    constraint_consistency: float,
    minimality: float,
    novelty: float,
) -> float:
    return round(
        0.45 * target_verification
        + 0.20 * collateral_verification
        + 0.10 * static_sanity
        + 0.10 * constraint_consistency
        + 0.10 * minimality
        + 0.05 * novelty,
        4,
    )
