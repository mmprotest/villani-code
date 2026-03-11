from __future__ import annotations

from pathlib import Path

from villani_code.localize.signals import WEIGHTS, score_file
from villani_code.runtime.schemas import Evidence, SuspectRegion


def rank_suspects(repo: Path, evidence: Evidence, candidate_files: list[str], recent_reads: set[str] | None = None, recent_edits: set[str] | None = None) -> list[SuspectRegion]:
    suspects: list[SuspectRegion] = []
    for rel in candidate_files:
        sig = score_file(rel, evidence, recent_reads, recent_edits)
        total = sum(sig[name] * WEIGHTS[name] for name in WEIGHTS)
        suspects.append(SuspectRegion(file=rel, score=round(total, 4), signal_breakdown=sig))
    return sorted(suspects, key=lambda s: s.score, reverse=True)
