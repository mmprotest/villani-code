from __future__ import annotations

import re
from pathlib import Path

from villani_code.localize.signals import WEIGHTS, score_file
from villani_code.runtime.schemas import Evidence, SuspectRegion


def _extract_evidence_text(evidence: Evidence) -> str:
    chunks = [
        *evidence.failing_tests,
        *evidence.error_messages,
        *evidence.repro_commands,
        *evidence.visible_verification_commands,
        *evidence.stack_traces,
    ]
    return "\n".join(chunks).lower()


def _lexical_overlap(path: str, evidence_text: str) -> float:
    tokens = [t for t in re.split(r"[^a-zA-Z0-9_]+", path.lower()) if len(t) > 2]
    if not tokens:
        return 0.0
    hits = sum(1 for t in set(tokens) if t in evidence_text)
    return min(1.0, hits / max(1, len(set(tokens))))


def rank_suspects(repo: Path, evidence: Evidence, candidate_files: list[str], recent_reads: set[str] | None = None, recent_edits: set[str] | None = None) -> list[SuspectRegion]:
    evidence_text = _extract_evidence_text(evidence)
    suspects: list[SuspectRegion] = []
    expected_single = evidence.benchmark_expected_files[0] if len(evidence.benchmark_expected_files) == 1 else ""
    for rel in candidate_files:
        sig = score_file(rel, evidence, recent_reads, recent_edits)
        sig["lexical"] = max(sig.get("lexical", 0.0), _lexical_overlap(rel, evidence_text))
        total = sum(sig[name] * WEIGHTS[name] for name in WEIGHTS)
        if expected_single and rel == expected_single:
            total += 1.0
            sig["expected_single_file_priority"] = 1.0
        elif any(rel in trace for trace in evidence.stack_traces):
            total += 0.7
            sig["stacktrace_path_priority"] = 1.0
        suspects.append(SuspectRegion(file=rel, score=round(total, 4), signal_breakdown=sig))
    return sorted(suspects, key=lambda s: s.score, reverse=True)
