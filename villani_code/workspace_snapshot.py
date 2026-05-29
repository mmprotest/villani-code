from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAX_WORKSPACE_SNAPSHOT_FILES = 5000
IGNORED_WORKSPACE_SNAPSHOT_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "target",
    "dist",
    "build",
}


@dataclass(frozen=True)
class WorkspaceFileEntry:
    path: str
    size: int
    mtime_ns: int | None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    files: dict[str, WorkspaceFileEntry]
    scanned_files: int
    truncated: bool = False
    unavailable: bool = False
    reason: str = ""


@dataclass(frozen=True)
class WorkspaceSnapshotDiff:
    added: int = 0
    removed: int = 0
    modified: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.modified)


def snapshot_workspace(root: Path | str | None, *, max_files: int = DEFAULT_MAX_WORKSPACE_SNAPSHOT_FILES) -> WorkspaceSnapshot:
    if root is None:
        return WorkspaceSnapshot(root="", files={}, scanned_files=0, unavailable=True, reason="missing_root")
    try:
        root_path = Path(root).resolve()
    except Exception as exc:
        return WorkspaceSnapshot(root=str(root), files={}, scanned_files=0, unavailable=True, reason=type(exc).__name__)
    if not root_path.exists() or not root_path.is_dir():
        return WorkspaceSnapshot(root=str(root_path), files={}, scanned_files=0, unavailable=True, reason="root_not_directory")

    files: dict[str, WorkspaceFileEntry] = {}
    scanned = 0
    truncated = False
    try:
        for current, dirnames, filenames in os.walk(root_path, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_WORKSPACE_SNAPSHOT_DIRS)
            for filename in sorted(filenames):
                if scanned >= max_files:
                    truncated = True
                    dirnames[:] = []
                    break
                path = Path(current) / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if not path.is_file():
                    continue
                try:
                    rel = path.relative_to(root_path).as_posix()
                except ValueError:
                    continue
                files[rel] = WorkspaceFileEntry(path=rel, size=stat.st_size, mtime_ns=getattr(stat, "st_mtime_ns", None))
                scanned += 1
            if truncated:
                break
    except Exception as exc:
        return WorkspaceSnapshot(
            root=str(root_path),
            files=files,
            scanned_files=scanned,
            truncated=truncated,
            unavailable=True,
            reason=type(exc).__name__,
        )
    return WorkspaceSnapshot(root=str(root_path), files=files, scanned_files=scanned, truncated=truncated)


def diff_workspace_snapshots(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> WorkspaceSnapshotDiff:
    before_paths = set(before.files)
    after_paths = set(after.files)
    added_paths = after_paths - before_paths
    removed_paths = before_paths - after_paths
    common_paths = before_paths & after_paths
    modified = sum(1 for path in common_paths if before.files[path] != after.files[path])
    return WorkspaceSnapshotDiff(added=len(added_paths), removed=len(removed_paths), modified=modified)
