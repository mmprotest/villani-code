from villani_code.orchestrate.merge import score_candidate
from villani_code.orchestrate.state import CandidatePatch, PatchUnit, WorkerReport


def _candidate(diff_text: str, files: list[str]) -> CandidatePatch:
    return CandidatePatch(
        worker_id="w1",
        patch_unit=PatchUnit(title="u", objective="o"),
        report=WorkerReport(status="success"),
        diff_text=diff_text,
        files_changed=files,
    )


def test_score_prefers_verified_minimal_and_evidence() -> None:
    a = _candidate("--- a/a.py\n+++ b/a.py\n+x\n", ["a.py"])
    b = _candidate("--- a/a.py\n+++ b/a.py\n" + "\n".join(["+x"] * 20), ["a.py", "b.py"])
    score_a = score_candidate(a, {"a.py"}, True)
    score_b = score_candidate(b, {"a.py"}, False)
    assert score_a > score_b
