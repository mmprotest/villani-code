from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path

from villani_code.benchmark.policy import is_runtime_artifact_path, normalize_path


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    digest: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    files: dict[str, FileFingerprint]


@dataclass(frozen=True)
class WorkspaceChangeSummary:
    created: list[str]
    modified: list[str]
    deleted: list[str]

    @property
    def changed_files(self) -> list[str]:
        return sorted(set(self.created + self.modified + self.deleted))


_DEFAULT_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
}


def snapshot_workspace(repo_path: Path, *, extra_ignored: set[str] | None = None) -> WorkspaceSnapshot:
    ignored = {normalize_path(item) for item in (extra_ignored or set())}
    files: dict[str, FileFingerprint] = {}
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        rel = normalize_path(str(path.relative_to(repo_path)))
        parts = set(rel.split("/"))
        if parts & _DEFAULT_IGNORED_DIRS:
            continue
        if _is_ignored(rel, ignored):
            continue
        files[rel] = _fingerprint(path)
    return WorkspaceSnapshot(files=files)


def diff_workspace(
    baseline: WorkspaceSnapshot,
    repo_path: Path,
    *,
    extra_ignored: set[str] | None = None,
) -> WorkspaceChangeSummary:
    current = snapshot_workspace(repo_path, extra_ignored=extra_ignored)
    before_paths = set(baseline.files)
    after_paths = set(current.files)

    created = sorted(after_paths - before_paths)
    deleted = sorted(before_paths - after_paths)
    modified = sorted(
        path
        for path in sorted(before_paths & after_paths)
        if baseline.files[path] != current.files[path]
    )
    return WorkspaceChangeSummary(created=created, modified=modified, deleted=deleted)


def _is_ignored(path: str, extra_ignored: set[str]) -> bool:
    if is_runtime_artifact_path(path):
        return True
    if path in extra_ignored:
        return True
    return any(path.startswith(f"{prefix}/") for prefix in extra_ignored)


def _fingerprint(path: Path) -> FileFingerprint:
    digest = sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return FileFingerprint(size=path.stat().st_size, digest=digest.hexdigest())
