from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

IGNORE_PATTERNS = (
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".villani_code/runs",
    ".villani_code/benchmark",
    ".villani_code/artifacts",
    ".benchmarks",
    "benchmark_outputs",
    "artifacts",
    "tmp",
    "temp",
    "node_modules",
)


@dataclass(slots=True)
class WorkspaceHandle:
    workspace: Path
    cleanup: Callable[[], None]
    strategy: str
    prep_seconds: float


def _copytree_ignore(_src: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in IGNORE_PATTERNS:
            ignored.add(name)
    return ignored


def prepare_candidate_workspace(repo_path: Path, *, fast_path: bool) -> WorkspaceHandle:
    started = time.monotonic()
    td = Path(tempfile.mkdtemp(prefix="villani-weak-search-"))
    workspace = td / "repo"

    git_dir = repo_path / ".git"
    if fast_path and git_dir.exists():
        worktree_name = f"cand-{int(time.time() * 1000)}"
        worktree_path = td / worktree_name
        proc = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            prep_seconds = time.monotonic() - started

            def _cleanup() -> None:
                subprocess.run(["git", "worktree", "remove", "--force", str(worktree_path)], cwd=repo_path, capture_output=True, text=True)
                shutil.rmtree(td, ignore_errors=True)

            return WorkspaceHandle(workspace=worktree_path, cleanup=_cleanup, strategy="git_worktree", prep_seconds=prep_seconds)

    shutil.copytree(repo_path, workspace, dirs_exist_ok=True, ignore=_copytree_ignore)
    prep_seconds = time.monotonic() - started

    def _cleanup_copy() -> None:
        shutil.rmtree(td, ignore_errors=True)

    return WorkspaceHandle(workspace=workspace, cleanup=_cleanup_copy, strategy="copytree_selective", prep_seconds=prep_seconds)


def cleanup_candidate_workspace(handle: WorkspaceHandle) -> None:
    handle.cleanup()
