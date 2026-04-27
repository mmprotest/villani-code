from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def get_current_branch(repo: Path) -> str:
    proc = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Failed to get current branch")
    return proc.stdout.strip()


def get_head_commit(repo: Path) -> str:
    proc = _git(repo, ["rev-parse", "HEAD"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Failed to get head commit")
    return proc.stdout.strip()


def create_worktree(repo: Path, mission_dir: Path, mission_id: str, task_id: str, base_commit: str) -> tuple[Path, str]:
    worktree = mission_dir / "orchestrator" / "worktrees" / task_id
    worktree.parent.mkdir(parents=True, exist_ok=True)
    branch_name = f"villani-orch-{mission_id}-{task_id}"
    proc = _git(repo, ["worktree", "add", "-B", branch_name, str(worktree), base_commit])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Failed to create worktree")
    return worktree, branch_name


def remove_worktree(worktree: Path) -> None:
    proc = _git(worktree, ["worktree", "remove", "--force", str(worktree)])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Failed to remove worktree")


def commit_all(worktree: Path, message: str) -> bool:
    if not has_diff(worktree):
        return False
    add_proc = _git(worktree, ["add", "-A"])
    if add_proc.returncode != 0:
        return False
    commit_proc = _git(worktree, ["commit", "-m", message])
    return commit_proc.returncode == 0


def merge_branch(repo: Path, branch_name: str) -> tuple[bool, str]:
    proc = _git(repo, ["merge", "--no-ff", "--no-edit", branch_name])
    return proc.returncode == 0, (proc.stdout + "\n" + proc.stderr).strip()


def changed_files(worktree: Path) -> list[str]:
    proc = _git(worktree, ["diff", "--name-only"])
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def has_diff(worktree: Path) -> bool:
    proc = _git(worktree, ["status", "--porcelain"])
    if proc.returncode != 0:
        return False
    return bool(proc.stdout.strip())


def diff_line_count(worktree: Path) -> int:
    proc = _git(worktree, ["diff", "--numstat"])
    if proc.returncode != 0:
        return 0
    count = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            add = 0 if parts[0] == "-" else int(parts[0])
            delete = 0 if parts[1] == "-" else int(parts[1])
        except ValueError:
            continue
        count += add + delete
    return count
