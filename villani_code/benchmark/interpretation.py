from __future__ import annotations

from dataclasses import dataclass


HEADLINE_COMPARABLE = "headline_comparable"
INFORMATIONAL_ONLY = "informational_only"
INTERNAL_ONLY = "internal_only"


@dataclass(frozen=True, slots=True)
class InterpretationResult:
    status: str
    warning: str | None


def derive_interpretation_status(
    *,
    pack_classification: str,
    comparison_suitability: str,
    run_fairness: str,
) -> InterpretationResult:
    if pack_classification == "internal_regression" or comparison_suitability == "internal_only":
        return InterpretationResult(
            status=INTERNAL_ONLY,
            warning="This run is internal-only and intended for regression tracking.",
        )

    if run_fairness == "mixed":
        return InterpretationResult(
            status=INFORMATIONAL_ONLY,
            warning="This run is informational only and should not be used as a headline cross-agent comparison.",
        )

    headline_pack = pack_classification in {"general_coding", "constrained_model"}
    headline_suitability = comparison_suitability in {
        "headline_comparison_suitable",
        "cross_agent_comparison",
        "comparable",
        "constrained_model_only",
    }
    if headline_pack and headline_suitability:
        return InterpretationResult(status=HEADLINE_COMPARABLE, warning=None)

    return InterpretationResult(
        status=INFORMATIONAL_ONLY,
        warning="This run is informational only and should not be used as a headline cross-agent comparison.",
    )
