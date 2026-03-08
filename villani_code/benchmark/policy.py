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
    ignored_junk_paths: list[str]
    meaningful_touched_paths: list[str]
    meaningful_expected_paths: list[str]
    meaningful_unexpected_paths: list[str]
    allowed_support_paths: list[str]
    violating_paths: list[str]
    forbidden_reason_detail: str | None


def _is_allowed_support_path(path: str, *, expected_paths: list[str], family: str, task_type: str | None, meaningful: list[str]) -> bool:
    if family == "terminal_workflow":
        if path in {"Makefile", "pyproject.toml"} or path.endswith("/__main__.py"):
            return True

    if task_type == "repo_navigation_bugfix" and path.endswith("/__init__.py"):
        parent = str(Path(path).parent)
        expected_parents = {str(Path(expected).parent) for expected in expected_paths}
        if parent in expected_parents:
            return True

    if family == "repro_test":
        touched_test = any(p.startswith("tests/") for p in meaningful)
        if touched_test and (path.startswith("src/") or path.startswith("app/")):
            return True

    return False


def enforce_path_policy(
    touched: list[str],
    allowlist: list[str],
    forbidden: list[str],
    *,
    expected_paths: list[str] | None = None,
    family: str = "",
    task_type: str | None = None,
) -> PolicyCheckResult:
    expected_paths = expected_paths or []
    allowlist_ok = all(any(path.startswith(prefix) for prefix in allowlist) for path in touched)
    forbidden_ok = not any(any(path.startswith(prefix) for prefix in forbidden) for path in touched)
    suspicious = [
        path for path in touched if path.endswith("conftest.py") or path.startswith(".github/") or path.startswith(".git/")
    ]
    meaningful_expected = [path for path in touched if path in expected_paths]
    meaningful_unexpected = [path for path in touched if path not in expected_paths]
    allowed_support = [
        path
        for path in meaningful_unexpected
        if _is_allowed_support_path(path, expected_paths=expected_paths, family=family, task_type=task_type, meaningful=touched)
    ]
    violating_paths = [path for path in meaningful_unexpected if path not in allowed_support]
    forbidden_reason_detail = None
    if violating_paths:
        forbidden_reason_detail = f"unexpected meaningful edits: {', '.join(violating_paths)}"
    return PolicyCheckResult(
        allowlist_ok=allowlist_ok,
        forbidden_ok=forbidden_ok,
        suspicious_patterns=suspicious,
        ignored_junk_paths=[],
        meaningful_touched_paths=touched,
        meaningful_expected_paths=meaningful_expected,
        meaningful_unexpected_paths=meaningful_unexpected,
        allowed_support_paths=allowed_support,
        violating_paths=violating_paths,
        forbidden_reason_detail=forbidden_reason_detail,
    )


def benchmark_asset_integrity(task_dir: Path) -> bool:
    return (task_dir / "task.yaml").exists() and (task_dir / "prompt.txt").exists() and (task_dir / "metadata.json").exists()
