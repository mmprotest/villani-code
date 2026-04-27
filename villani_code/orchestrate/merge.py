from __future__ import annotations

import subprocess
from pathlib import Path

from villani_code.orchestrate.state import CandidatePatch


def score_candidate(candidate: CandidatePatch, evidence_files: set[str], verification_passed: bool) -> tuple[int, int, int, int, int, int]:
    diff_lines = [line for line in candidate.diff_text.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    touched = set(candidate.files_changed)
    evidence_hits = len(touched & evidence_files)
    formatting_churn = sum(1 for line in diff_lines if line.strip() in {"+", "-"})
    broad_rewrite = 1 if len(diff_lines) > 400 or len(touched) > 8 else 0
    return (
        1 if verification_passed else 0,
        -len(diff_lines),
        -len(touched),
        evidence_hits,
        -formatting_churn,
        -broad_rewrite,
    )


def apply_diff(repo: Path, diff_text: str) -> bool:
    if not diff_text.strip():
        return False
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=repo,
        text=True,
        input=diff_text,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0
