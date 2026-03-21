from __future__ import annotations

import subprocess
from pathlib import Path

from villani_code.benchmark.policy import filter_meaningful_touched_paths


def _run(repo: Path, args: list[str]) -> str:
    proc = subprocess.run(args, cwd=repo, text=True, capture_output=True, check=False)
    return proc.stdout


def list_touched_files(repo: Path) -> list[str]:
    tracked = set(_run(repo, ["git", "diff", "--name-only"]).splitlines())
    untracked = set(_run(repo, ["git", "ls-files", "--others", "--exclude-standard"]).splitlines())
    return sorted(path for path in (tracked | untracked) if path)


def line_stats(repo: Path, *, require_patch_artifact: bool = False) -> tuple[int, int]:
    raw_tracked = _run(repo, ["git", "diff", "--name-only"]).splitlines()
    raw_untracked = _run(repo, ["git", "ls-files", "--others", "--exclude-standard"]).splitlines()
    meaningful_tracked = filter_meaningful_touched_paths(raw_tracked, require_patch_artifact=require_patch_artifact)
    meaningful_untracked = filter_meaningful_touched_paths(raw_untracked, require_patch_artifact=require_patch_artifact)

    added = 0
    deleted = 0
    if meaningful_tracked:
        proc = subprocess.run(
            ["git", "diff", "--numstat", "--", *meaningful_tracked],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            if parts[0].isdigit():
                added += int(parts[0])
            if parts[1].isdigit():
                deleted += int(parts[1])

    for path in meaningful_untracked:
        full = repo / path
        if full.exists():
            try:
                added += len(full.read_text(encoding="utf-8").splitlines())
            except UnicodeDecodeError:
                continue
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
