from pathlib import Path

from villani_code.runtime.workspace import prepare_candidate_workspace


def test_workspace_fast_path_prefers_non_full_copy(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "big.bin").write_bytes(b"0" * 64)

    handle = prepare_candidate_workspace(tmp_path, fast_path=True)
    try:
        assert handle.strategy in {"copytree_selective", "git_worktree"}
        assert (handle.workspace / "src" / "app.py").exists()
        assert not (handle.workspace / ".venv" / "big.bin").exists()
        assert handle.prep_seconds >= 0.0
    finally:
        handle.cleanup()
