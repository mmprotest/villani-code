from villani_code.benchmark.interpretation import derive_interpretation_status


def test_internal_pack_is_never_headline() -> None:
    result = derive_interpretation_status(
        pack_classification="internal_regression",
        comparison_suitability="internal_only",
        run_fairness="same-backend",
    )
    assert result.status == "internal_only"


def test_mixed_fairness_is_informational_only() -> None:
    result = derive_interpretation_status(
        pack_classification="general_coding",
        comparison_suitability="headline_comparison_suitable",
        run_fairness="mixed",
    )
    assert result.status == "informational_only"


def test_general_pack_same_backend_can_be_headline() -> None:
    result = derive_interpretation_status(
        pack_classification="general_coding",
        comparison_suitability="headline_comparison_suitable",
        run_fairness="same-backend",
    )
    assert result.status == "headline_comparable"
