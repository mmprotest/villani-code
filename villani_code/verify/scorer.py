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
        0.60 * target_verification
        + 0.12 * collateral_verification
        + 0.08 * static_sanity
        + 0.10 * constraint_consistency
        + 0.07 * minimality
        + 0.03 * novelty,
        4,
    )
