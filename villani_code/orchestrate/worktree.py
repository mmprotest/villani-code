from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Workspace:
    path: Path
    cleanup_path: Path
    mode: str
    branch: str


class WorkspaceManager:
    def __init__(self, repo: Path, keep_worktrees: bool = False, prefer_git_worktree: bool = True) -> None:
        self.repo = repo
        self.keep_worktrees = keep_worktrees
        self.prefer_git_worktree = prefer_git_worktree

    def _git_worktree_available(self) -> bool:
        proc = subprocess.run(["git", "--version"], cwd=self.repo, text=True, capture_output=True, check=False)
        return proc.returncode == 0

    def create(self, prefix: str) -> Workspace:
        slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
        root = Path(tempfile.mkdtemp(prefix=f"villani-orch-{slug}-"))
        workspace = root / "repo"
        if self.prefer_git_worktree and self._git_worktree_available():
            branch = f"villani/orchestrate/{slug}"
            proc = subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(workspace), "HEAD"],
                cwd=self.repo,
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0:
                return Workspace(path=workspace, cleanup_path=workspace, mode="git-worktree", branch=branch)

        shutil.copytree(self.repo, workspace, dirs_exist_ok=True)
        git_dir = workspace / ".git"
        if git_dir.exists() and git_dir.is_file():
            git_dir.unlink(missing_ok=True)
        if (workspace / ".git").exists() and (workspace / ".git").is_dir():
            shutil.rmtree(workspace / ".git", ignore_errors=True)
        subprocess.run(["git", "init"], cwd=workspace, text=True, capture_output=True, check=False)
        subprocess.run(["git", "add", "-A"], cwd=workspace, text=True, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "workspace snapshot"], cwd=workspace, text=True, capture_output=True, check=False)
        return Workspace(path=workspace, cleanup_path=root, mode="copy", branch="")

    def cleanup(self, workspace: Workspace) -> None:
        if self.keep_worktrees:
            return
        if workspace.mode == "git-worktree":
            subprocess.run(["git", "worktree", "remove", "--force", str(workspace.path)], cwd=self.repo, text=True, capture_output=True, check=False)
            if workspace.branch:
                subprocess.run(["git", "branch", "-D", workspace.branch], cwd=self.repo, text=True, capture_output=True, check=False)
            return
        shutil.rmtree(workspace.cleanup_path, ignore_errors=True)


def git_diff_text(repo: Path) -> str:
    proc = subprocess.run(["git", "diff", "--no-ext-diff"], cwd=repo, text=True, capture_output=True, check=False)
    return proc.stdout or ""


def git_changed_files(repo: Path) -> list[str]:
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=False)
    files: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line[3:].strip()
        if parts:
            files.append(parts)
    return files


def is_dirty(repo: Path) -> bool:
    return bool(git_changed_files(repo))
