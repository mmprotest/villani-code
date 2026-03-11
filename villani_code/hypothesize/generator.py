from __future__ import annotations

from villani_code.hypothesize.diversity import dedup_hypotheses, diversity_bucket
from villani_code.runtime.schemas import HypothesisClass, HypothesisRecord, SuspectRegion


DEFAULT_CLASSES = [
    HypothesisClass.CONTRACT_MISMATCH,
    HypothesisClass.NULL_OR_EMPTY_CASE,
    HypothesisClass.BOUNDARY_ERROR,
    HypothesisClass.PATH_OR_IMPORT_ERROR,
]


def generate_hypotheses(suspect: SuspectRegion, objective: str, max_items: int = 5) -> tuple[list[HypothesisRecord], list[HypothesisRecord]]:
    generated: list[HypothesisRecord] = []
    for idx, cls in enumerate(DEFAULT_CLASSES[:max(3, min(7, max_items))], start=1):
        text = f"{suspect.file}: {cls.value.replace('_', ' ')} may violate objective '{objective[:80]}'"
        generated.append(
            HypothesisRecord(
                id=f"hyp-{suspect.file}-{idx}".replace("/", "_"),
                suspect_ref=suspect.file,
                text=text,
                hypothesis_class=cls,
                plausibility_score=max(0.1, suspect.score - (idx * 0.05)),
                diversity_bucket=diversity_bucket(cls),
            )
        )
    return dedup_hypotheses(generated)
