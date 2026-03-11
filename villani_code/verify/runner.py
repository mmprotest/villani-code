from __future__ import annotations

import hashlib
from dataclasses import dataclass

from villani_code.verify.gates import GateResult, hard_fail_gate
from villani_code.verify.scorer import score_patch


@dataclass(slots=True)
class VerificationResult:
    gate: GateResult
    score: float
    fingerprint: str
    outputs: dict[str, object]


def verification_fingerprint(outputs: dict[str, object]) -> str:
    stable = "|".join(f"{k}:{outputs.get(k)}" for k in sorted(outputs))
    return hashlib.sha1(stable.encode("utf-8")).hexdigest()


def run_staged_verifier(outputs: dict[str, object]) -> VerificationResult:
    gate = hard_fail_gate(outputs)
    score = 0.0
    if not gate.hard_fail:
        score = score_patch(
            target_verification=float(outputs.get("target_verification", 0.0)),
            collateral_verification=float(outputs.get("collateral_verification", 0.0)),
            static_sanity=float(outputs.get("static_sanity", 1.0)),
            constraint_consistency=float(outputs.get("constraint_consistency", 1.0)),
            minimality=float(outputs.get("minimality", 1.0)),
            novelty=float(outputs.get("novelty", 0.5)),
        )
    return VerificationResult(gate=gate, score=score, fingerprint=verification_fingerprint(outputs), outputs=outputs)
