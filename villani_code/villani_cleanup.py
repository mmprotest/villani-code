from __future__ import annotations

from pathlib import Path

_SCRATCH_EXACT = {
    "run_tests.py",
    "final_test.py",
    "test_quick.py",
    "list_files.py",
}



def is_probable_scratch_file(path: str) -> bool:
    name = Path(path).name.lower()
    if name in _SCRATCH_EXACT:
        return True
    return (
        name.startswith("debug_")
        or name.startswith("verify_")
        or name.startswith("tmp_")
        or name.startswith("probe_")
        or name.endswith("_scratch.py")
    )



def detect_scratch_files(paths: list[str]) -> list[str]:
    return sorted({p for p in paths if is_probable_scratch_file(p)})



def cleanup_candidates(scratch_files: list[str]) -> list[str]:
    return sorted({p for p in scratch_files if is_probable_scratch_file(p)})
