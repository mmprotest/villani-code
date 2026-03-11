from __future__ import annotations

from dataclasses import dataclass

from villani_code.runtime.schemas import HypothesisRecord


@dataclass(slots=True)
class PatchCandidate:
    id: str
    branch_id: str
    files_touched: list[str]
    changed_lines: int
    hunks: int
    summary: str


def generate_patch_candidates(hypothesis: HypothesisRecord, branch_id: str, max_candidates: int = 2) -> list[PatchCandidate]:
    candidates: list[PatchCandidate] = []
    for i in range(1, min(max_candidates, 3) + 1):
        candidates.append(
            PatchCandidate(
                id=f"cand-{branch_id}-{i}",
                branch_id=branch_id,
                files_touched=[hypothesis.suspect_ref],
                changed_lines=8 + i,
                hunks=1,
                summary=f"Tiny edit for {hypothesis.hypothesis_class.value}",
            )
        )
    return candidates
