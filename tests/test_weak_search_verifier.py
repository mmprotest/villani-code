from villani_code.verify.gates import hard_fail_gate
from villani_code.verify.runner import run_staged_verifier
from villani_code.verify.scorer import score_patch


def test_verifier_hard_fail_gating():
    gate = hard_fail_gate({"patch_applies": False})
    assert gate.hard_fail


def test_patch_scoring():
    score = score_patch(target_verification=1, collateral_verification=0.5, static_sanity=1, constraint_consistency=1, minimality=1, novelty=0.5)
    assert score > 0.8


def test_staged_verifier_returns_fingerprint():
    result = run_staged_verifier({"patch_applies": True, "syntax_ok": True, "imports_ok": True, "target_verification": 1, "collateral_verification": 1, "static_sanity": 1, "constraint_consistency": 1, "minimality": 1, "novelty": 1})
    assert result.fingerprint
    assert result.score > 0
