from pathlib import Path

from villani_code.localize.ranker import rank_suspects
from villani_code.runtime.schemas import Evidence


def test_suspect_ranking_order():
    evidence = Evidence(stack_traces=["src/app/core.py:10"], benchmark_expected_files=["src/app/core.py"])
    suspects = rank_suspects(Path('.'), evidence, ["src/app/core.py", "README.md"])
    assert suspects[0].file == "src/app/core.py"
    assert suspects[0].score >= suspects[1].score


def test_localization_prefers_failure_evidence_over_allowlist():
    evidence = Evidence(
        error_messages=["failure in src/core/bug.py"],
        benchmark_allowlist_paths=["src/other.py", "src/core/bug.py"],
    )
    suspects = rank_suspects(Path("."), evidence, ["src/other.py", "src/core/bug.py"])
    assert suspects[0].file == "src/core/bug.py"
