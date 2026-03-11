from __future__ import annotations

from pathlib import Path

from villani_code.runtime.schemas import Evidence


WEIGHTS = {
    "stacktrace": 0.30,
    "failing_test": 0.20,
    "symbol_proximity": 0.15,
    "import_proximity": 0.10,
    "lexical": 0.10,
    "benchmark_prior": 0.10,
    "recent_proximity": 0.05,
}


def score_file(path: str, evidence: Evidence, recent_reads: set[str] | None = None, recent_edits: set[str] | None = None) -> dict[str, float]:
    recent_reads = recent_reads or set()
    recent_edits = recent_edits or set()
    base = {k: 0.0 for k in WEIGHTS}
    norm = path.replace("\\", "/").lstrip("./")
    if any(norm in trace for trace in evidence.stack_traces):
        base["stacktrace"] = 1.0
    if any(Path(test).stem.replace("test_", "") in norm for test in evidence.failing_tests):
        base["failing_test"] = 1.0
    if any(token in " ".join(evidence.error_messages).lower() for token in [Path(norm).stem.lower(), norm.lower()]):
        base["lexical"] = 1.0
    if norm in evidence.benchmark_expected_files or any(norm.startswith(p.rstrip("/")) for p in evidence.benchmark_allowlist_paths):
        base["benchmark_prior"] = 1.0
    if norm in recent_reads or norm in recent_edits:
        base["recent_proximity"] = 1.0
    return base
