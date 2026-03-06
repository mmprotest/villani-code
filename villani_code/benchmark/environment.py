from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class BenchmarkWorkspace:
    source_repo: Path
    workspace_root: Path
    work_repo: Path


class BenchmarkEnvironment:
    def __init__(self) -> None:
        self._workspaces: list[Path] = []

    def create_workspace(self, source_repo: Path, repo_git_ref: str | None = None) -> BenchmarkWorkspace:
        source_repo = source_repo.resolve()
        temp_root = Path(tempfile.mkdtemp(prefix="villani-benchmark-"))
        self._workspaces.append(temp_root)
        work_repo = temp_root / source_repo.name

        git_dir = source_repo / ".git"
        if git_dir.exists():
            clone_cmd = ["git", "clone", "--quiet", str(source_repo), str(work_repo)]
            proc = subprocess.run(clone_cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                self._copy_repo(source_repo, work_repo)
        else:
            self._copy_repo(source_repo, work_repo)

        if repo_git_ref:
            subprocess.run(
                ["git", "checkout", repo_git_ref],
                cwd=work_repo,
                capture_output=True,
                text=True,
                check=False,
            )

        return BenchmarkWorkspace(source_repo=source_repo, workspace_root=temp_root, work_repo=work_repo)

    def collect_changed_files(self, work_repo: Path) -> list[str]:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=work_repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return []
        files: list[str] = []
        for line in proc.stdout.splitlines():
            if len(line) < 4:
                continue
            files.append(line[3:])
        return sorted(set(files))

    def collect_git_diff(self, work_repo: Path) -> str:
        proc = subprocess.run(
            ["git", "diff", "--no-color"],
            cwd=work_repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout

    def cleanup(self) -> None:
        for workspace in reversed(self._workspaces):
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)
        self._workspaces.clear()

    @staticmethod
    def _copy_repo(source_repo: Path, work_repo: Path) -> None:
        shutil.copytree(
            source_repo,
            work_repo,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc"),
        )
