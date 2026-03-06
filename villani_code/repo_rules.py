from __future__ import annotations

from pathlib import Path

_IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    ".vscode",
    ".idea",
    ".villani_code",
    "build",
    "dist",
    "node_modules",
}

_RUNTIME_DIR_NAMES = {".ipynb_checkpoints", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_EDITOR_DIR_NAMES = {".vscode", ".idea"}
_GENERATED_DIR_NAMES = {"build", "dist", "node_modules"}
_RUNTIME_SUFFIXES = {".pyc", ".pyo", ".pyd"}
_EDITOR_FILES = {".DS_Store", "Thumbs.db"}
_DOC_SUFFIXES = {".md", ".rst", ".txt"}


def _normalize(path: str | Path) -> Path:
    if isinstance(path, Path):
        return path
    return Path(path)


def _parts(path: str | Path) -> tuple[str, ...]:
    return _normalize(path).as_posix().split("/")


def _contains_dir(path: str | Path, names: set[str]) -> bool:
    return any(part in names for part in _parts(path))


def is_ignored_repo_path(path: str | Path) -> bool:
    p = _normalize(path)
    name = p.name
    if name in _EDITOR_FILES:
        return True
    if p.suffix.lower() in _RUNTIME_SUFFIXES:
        return True
    return _contains_dir(p, _IGNORED_DIR_NAMES)


def classify_repo_path(path: str | Path) -> str:
    p = _normalize(path)
    parts = _parts(p)
    name = p.name
    if ".git" in parts:
        return "vcs_internal"
    if name in _EDITOR_FILES or any(part in _EDITOR_DIR_NAMES for part in parts):
        return "editor_artifact"
    if p.suffix.lower() in _RUNTIME_SUFFIXES or any(part in _RUNTIME_DIR_NAMES for part in parts):
        return "runtime_artifact"
    if any(part in _GENERATED_DIR_NAMES for part in parts):
        return "generated"
    if name.startswith(".") and name not in {".env.example"}:
        return "unknown"
    return "authoritative"


def is_authoritative_doc_path(path: str | Path) -> bool:
    p = _normalize(path)
    if is_ignored_repo_path(p):
        return False
    cls = classify_repo_path(p)
    if cls != "authoritative":
        return False

    parts = _parts(p)
    if not parts:
        return False

    if len(parts) == 1 and p.name in {"README.md", "README.rst"}:
        return True

    if parts[0] == "docs" and p.suffix.lower() in _DOC_SUFFIXES:
        return True

    if len(parts) == 1 and p.suffix.lower() in {".md", ".rst"} and not p.name.startswith("."):
        return True

    return False
