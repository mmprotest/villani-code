from __future__ import annotations

from collections import Counter

from villani_code.runtime.schemas import HypothesisClass, HypothesisRecord


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def dedup_hypotheses(items: list[HypothesisRecord]) -> tuple[list[HypothesisRecord], list[HypothesisRecord]]:
    kept: list[HypothesisRecord] = []
    rejected: list[HypothesisRecord] = []
    seen: list[set[str]] = []
    for item in items:
        tokens = set(_norm(item.text).split())
        duplicate = False
        for prev in seen:
            overlap = len(tokens & prev) / max(1, len(tokens | prev))
            if overlap >= 0.8:
                duplicate = True
                break
        if duplicate:
            item.status = "rejected_duplicate"
            rejected.append(item)
            continue
        seen.append(tokens)
        kept.append(item)

    if len(kept) >= 3:
        classes = Counter(i.hypothesis_class for i in kept)
        if len(classes) < 2 and kept[0].plausibility_score < 0.95:
            demoted = kept.pop()
            demoted.status = "rejected_low_diversity"
            rejected.append(demoted)
    return kept, rejected


def diversity_bucket(cls: HypothesisClass) -> str:
    return cls.value
