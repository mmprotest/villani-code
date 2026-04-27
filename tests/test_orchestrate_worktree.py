import subprocess
from pathlib import Path

from villani_code.orchestrate.worktree import WorkspaceManager


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=path, check=True, capture_output=True, text=True)
    (path / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)


def test_workspace_copy_cleanup(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    manager = WorkspaceManager(repo, keep_worktrees=False, prefer_git_worktree=False)
    ws = manager.create("unit")
    assert ws.path.exists()
    (ws.path / "new.txt").write_text("x\n", encoding="utf-8")
    manager.cleanup(ws)
    assert not ws.cleanup_path.exists()
