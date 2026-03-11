from villani_code.hypothesize.diversity import dedup_hypotheses
from villani_code.runtime.schemas import HypothesisClass, HypothesisRecord


def test_hypothesis_dedup_and_diversity_rules():
    items = [
        HypothesisRecord(id="1", suspect_ref="a.py", text="null check missing", hypothesis_class=HypothesisClass.NULL_OR_EMPTY_CASE, plausibility_score=0.8, diversity_bucket="a"),
        HypothesisRecord(id="2", suspect_ref="a.py", text="null   check missing", hypothesis_class=HypothesisClass.NULL_OR_EMPTY_CASE, plausibility_score=0.7, diversity_bucket="a"),
        HypothesisRecord(id="3", suspect_ref="a.py", text="contract mismatch at return", hypothesis_class=HypothesisClass.CONTRACT_MISMATCH, plausibility_score=0.7, diversity_bucket="b"),
    ]
    kept, rejected = dedup_hypotheses(items)
    assert len(kept) == 2
    assert rejected
