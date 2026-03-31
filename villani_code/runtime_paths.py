from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

LEGACY_STATE_DIRS = (".villani", ".villani_code")


def resolve_state_root(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env_value = os.environ.get("VILLANI_STATE_DIR", "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            return (Path(local) / "VillaniCode" / "state").resolve()
        return (Path(tempfile.gettempdir()) / "villani-code" / "state").resolve()
    return (Path.home() / ".local" / "state" / "villani-code").resolve()


def get_state_root() -> Path:
    return resolve_state_root()


def get_repo_id(repo: Path) -> str:
    resolved = repo.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    stem = resolved.name or "repo"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in stem).strip("-") or "repo"
    return f"{safe}-{digest}"


def get_repo_state_dir(repo: Path, explicit_root: Path | None = None) -> Path:
    return resolve_state_root(explicit_root) / get_repo_id(repo)


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_memory_dir(repo: Path, explicit_root: Path | None = None) -> Path:
    return _ensure(get_repo_state_dir(repo, explicit_root) / "memory")


def get_artifacts_dir(repo: Path, explicit_root: Path | None = None) -> Path:
    return _ensure(get_repo_state_dir(repo, explicit_root) / "artifacts")


def get_checkpoints_dir(repo: Path, explicit_root: Path | None = None) -> Path:
    return _ensure(get_repo_state_dir(repo, explicit_root) / "checkpoints")


def get_transcripts_dir(repo: Path, explicit_root: Path | None = None) -> Path:
    return _ensure(get_repo_state_dir(repo, explicit_root) / "transcripts")


def get_debug_dir(repo: Path, explicit_root: Path | None = None) -> Path:
    return _ensure(get_repo_state_dir(repo, explicit_root) / "debug")


def legacy_state_paths(repo: Path) -> list[Path]:
    base = repo.resolve()
    return [base / entry for entry in LEGACY_STATE_DIRS]
