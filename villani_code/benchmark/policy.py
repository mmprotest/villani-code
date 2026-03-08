from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path


RUNTIME_ARTIFACT_PATTERNS = [
    ".villani/**",
    ".villani_code/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.egg-info/**",
    "build/**",
    "dist/**",
    ".pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
]


def _normalize_path(path: str) -> str:
    normalized = path.replace('\\', '/')
    while normalized.startswith('./'):
        normalized = normalized[2:]
    return normalized


def is_runtime_artifact_path(path: str) -> bool:
    normalized = _normalize_path(path)
    for pattern in RUNTIME_ARTIFACT_PATTERNS:
        if fnmatch(normalized, pattern):
            return True
    return False


def filter_meaningful_touched_paths(touched: list[str]) -> list[str]:
    return [path for path in touched if not is_runtime_artifact_path(path)]


@dataclass
class PolicyCheckResult:
    allowlist_ok: bool
    forbidden_ok: bool
    suspicious_patterns: list[str]


def enforce_path_policy(touched: list[str], allowlist: list[str], forbidden: list[str]) -> PolicyCheckResult:
    allowlist_ok = all(any(path.startswith(prefix) for prefix in allowlist) for path in touched)
    forbidden_ok = not any(any(path.startswith(prefix) for prefix in forbidden) for path in touched)
    suspicious = [
        path for path in touched if path.endswith("conftest.py") or path.startswith(".github/") or path.startswith(".git/")
    ]
    return PolicyCheckResult(allowlist_ok=allowlist_ok, forbidden_ok=forbidden_ok, suspicious_patterns=suspicious)


def benchmark_asset_integrity(task_dir: Path) -> bool:
    return (task_dir / "task.yaml").exists() and (task_dir / "prompt.txt").exists() and (task_dir / "metadata.json").exists()
