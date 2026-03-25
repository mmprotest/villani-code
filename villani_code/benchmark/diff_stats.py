from __future__ import annotations

import subprocess
from pathlib import Path


def _run(repo: Path, args: list[str]) -> str:
    proc = subprocess.run(args, cwd=repo, text=True, capture_output=True, check=False)
    return proc.stdout


def list_touched_files(repo: Path) -> list[str]:
    tracked = set(_run(repo, ["git", "diff", "--name-only"]).splitlines())
    untracked = set(_run(repo, ["git", "ls-files", "--others", "--exclude-standard"]).splitlines())
    return sorted(path for path in (tracked | untracked) if path)


def _count_utf8_lines(path: Path) -> int | None:
    try:
        if not path.is_file():
            return None
        return len(path.read_text(encoding="utf-8").splitlines())
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError, UnicodeDecodeError):
        return None


def line_stats(repo: Path) -> tuple[int, int]:
    proc = subprocess.run(["git", "diff", "--numstat"], cwd=repo, text=True, capture_output=True, check=False)
    added = 0
    deleted = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            deleted += int(parts[1])
    for path in _run(repo, ["git", "ls-files", "--others", "--exclude-standard"]).splitlines():
        count = _count_utf8_lines(repo / path)
        if count is not None:
            added += count
    return added, deleted


def ensure_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.email=bench@example.com", "-c", "user.name=bench", "commit", "-m", "baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
