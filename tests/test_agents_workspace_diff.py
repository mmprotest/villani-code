from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.agents.workspace_diff import diff_workspace, snapshot_workspace


def test_workspace_diff_detects_created_modified_deleted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.py").write_text("a=1\n", encoding="utf-8")
    (repo / "delete.txt").write_text("bye\n", encoding="utf-8")

    baseline = snapshot_workspace(repo)

    (repo / "keep.py").write_text("a=2\n", encoding="utf-8")
    (repo / "new.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "delete.txt").unlink()

    changes = diff_workspace(baseline, repo)
    assert changes.created == ["new.py"]
    assert changes.modified == ["keep.py"]
    assert changes.deleted == ["delete.txt"]


def test_workspace_diff_ignores_runtime_and_debug_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("x=1\n", encoding="utf-8")
    baseline = snapshot_workspace(repo)

    debug_dir = repo / ".villani_code"
    debug_dir.mkdir()
    (debug_dir / "state.json").write_text("{}", encoding="utf-8")
    hook_log = repo / "claude_hook_events.jsonl"
    hook_log.write_text("{}\n", encoding="utf-8")

    changes = diff_workspace(baseline, repo, extra_ignored={"claude_hook_events.jsonl"})
    assert changes.changed_files == []
