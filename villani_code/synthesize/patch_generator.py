from __future__ import annotations

from dataclasses import dataclass

from villani_code.runtime.schemas import HypothesisRecord


@dataclass(slots=True)
class PatchCandidateRequest:
    id: str
    branch_id: str
    hypothesis_id: str
    prompt_summary: str


def generate_patch_candidates(hypothesis: HypothesisRecord, branch_id: str, max_candidates: int = 2) -> list[PatchCandidateRequest]:
    candidates: list[PatchCandidateRequest] = []
    for i in range(1, min(max_candidates, 3) + 1):
        candidates.append(
            PatchCandidateRequest(
                id=f"cand-{branch_id}-{i}",
                branch_id=branch_id,
                hypothesis_id=hypothesis.id,
                prompt_summary=f"Candidate request for {hypothesis.hypothesis_class.value}: {hypothesis.text[:120]}",
            )
        )
    return candidates
