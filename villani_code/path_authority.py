from __future__ import annotations

from pathlib import Path

INTERNAL_VILLANI_ROOTS: tuple[str, ...] = (".villani", ".villani_code")
SESSION_RESIDUE_MARKERS: tuple[str, ...] = (
    "session",
    "transcript",
    "context_state",
    "tool_inventory",
    "interrupted",
    "stale",
)


def _normalized_rel(path: str | Path, repo_root: str | Path | None = None) -> str:
    p = Path(path)
    root = Path(repo_root) if repo_root is not None else None
    if p.is_absolute() and root is not None:
        try:
            p = p.resolve().relative_to(root.resolve())
        except Exception:
            return ""
    rel = p.as_posix().strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def is_internal_villani_path(path: str | Path, repo_root: str | Path | None = None) -> bool:
    rel = _normalized_rel(path, repo_root=repo_root)
    if not rel:
        return False
    return any(rel == root or rel.startswith(f"{root}/") for root in INTERNAL_VILLANI_ROOTS)


def is_internal_villani_dir(path: str | Path, repo_root: str | Path | None = None) -> bool:
    rel = _normalized_rel(path, repo_root=repo_root)
    return rel in INTERNAL_VILLANI_ROOTS


def is_user_workspace_path(path: str | Path, repo_root: str | Path) -> bool:
    root = Path(repo_root).resolve()
    p = Path(path)
    try:
        candidate = p.resolve() if p.is_absolute() else (root / p).resolve()
        candidate.relative_to(root)
    except Exception:
        return False
    rel = _normalized_rel(candidate, repo_root=root)
    return bool(rel) and not is_internal_villani_path(rel)


def should_ignore_for_greenfield_context(path: str | Path, repo_root: str | Path | None = None) -> bool:
    if is_internal_villani_path(path, repo_root=repo_root):
        return True
    low = _normalized_rel(path, repo_root=repo_root).lower()
    return any(marker in low for marker in SESSION_RESIDUE_MARKERS)


def should_ignore_for_verification(path: str | Path, repo_root: str | Path | None = None) -> bool:
    return is_internal_villani_path(path, repo_root=repo_root)


def split_internal_paths(
    paths: list[str] | tuple[str, ...],
    *,
    repo_root: str | Path | None = None,
) -> tuple[list[str], list[str]]:
    user_paths: list[str] = []
    internal_paths: list[str] = []
    for raw in paths:
        rel = _normalized_rel(raw, repo_root=repo_root) or str(raw).strip()
        if not rel:
            continue
        if is_internal_villani_path(rel):
            internal_paths.append(rel)
        else:
            user_paths.append(rel)
    return sorted(dict.fromkeys(user_paths)), sorted(dict.fromkeys(internal_paths))
