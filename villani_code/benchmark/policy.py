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
    ".cache/**",
    "**/.cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "tmp/**",
    "temp/**",
    ".tmp/**",
    "**/*.tmp",
    "**/*.temp",
    "htmlcov/**",
    ".coverage*",
]

PATH_CLASS_IGNORED_RUNTIME_ARTIFACT = "ignored_runtime_artifact"
PATH_CLASS_EXACT_EXPECTED = "exact_expected"
PATH_CLASS_TASK_ADJACENT_SUPPORT = "task_adjacent_support"
PATH_CLASS_METADATA_OMISSION_REASONABLE = "metadata_omission_reasonable"
PATH_CLASS_CLEARLY_UNRELATED = "clearly_unrelated_meaningful_edit"


def normalize_path(path: str) -> str:
    normalized = str(path).replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if normalized.endswith("/") and normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized


def comparison_key(path: str) -> str:
    return normalize_path(path).casefold()


def paths_equal(path_a: str, path_b: str) -> bool:
    return comparison_key(path_a) == comparison_key(path_b)


def path_is_within(parent: str, child: str) -> bool:
    parent_norm = normalize_path(parent)
    child_norm = normalize_path(child)
    parent_key = comparison_key(parent_norm)
    child_key = comparison_key(child_norm)
    return child_key == parent_key or child_key.startswith(f"{parent_key}/")


def path_matches_glob(path: str, pattern: str) -> bool:
    return fnmatch(comparison_key(path), comparison_key(pattern))


def _path_matches_scope(path: str, scope: str) -> bool:
    scope_norm = normalize_path(scope)
    if scope.endswith("/"):
        return path_is_within(scope_norm, path)
    return paths_equal(path, scope_norm)


def is_runtime_artifact_path(path: str) -> bool:
    normalized = normalize_path(path)
    for pattern in RUNTIME_ARTIFACT_PATTERNS:
        if path_matches_glob(normalized, pattern):
            return True
    return False


def unique_normalized_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = normalize_path(path)
        if not normalized:
            continue
        key = comparison_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def filter_meaningful_touched_paths(touched: list[str]) -> list[str]:
    return [path for path in unique_normalized_paths(touched) if not is_runtime_artifact_path(path)]


@dataclass
class ClassifiedPath:
    path: str
    classification: str
    reason: str


@dataclass
class PolicyCheckResult:
    allowlist_ok: bool
    forbidden_ok: bool
    suspicious_patterns: list[str]
    ignored_junk_paths: list[str]
    normalized_touched_paths: list[str]
    path_classifications: dict[str, str]
    path_classification_reasons: dict[str, str]
    meaningful_touched_paths: list[str]
    meaningful_expected_paths: list[str]
    meaningful_unexpected_paths: list[str]
    allowed_support_paths: list[str]
    metadata_omission_paths: list[str]
    violating_paths: list[str]
    forbidden_reason_detail: str | None


def _has_parent_overlap(path: str, expected_paths: list[str]) -> bool:
    path_parent = normalize_path(str(Path(path).parent))
    expected_parents = {normalize_path(str(Path(expected).parent)) for expected in expected_paths}
    return path_parent in expected_parents


def _is_task_adjacent_support(
    path: str,
    *,
    expected_paths: list[str],
    family: str,
    task_type: str | None,
    allowed_support_files: list[str],
    allowed_support_globs: list[str],
) -> bool:
    if any(paths_equal(path, support) for support in allowed_support_files):
        return True
    if any(path_matches_glob(path, pattern) for pattern in allowed_support_globs):
        return True

    path_name = Path(path).name
    if family == "terminal_workflow":
        if path in {"Makefile", "pyproject.toml"}:
            return True
        if path_name in {"__main__.py", "main.py"} and _has_parent_overlap(path, expected_paths):
            return True

    if path_name == "__init__.py" and _has_parent_overlap(path, expected_paths):
        return True

    if task_type == "repo_navigation_bugfix" and path.endswith(".py") and _has_parent_overlap(path, expected_paths):
        return True

    if family == "localize_patch" and path.endswith(".py") and _has_parent_overlap(path, expected_paths):
        return True

    if family == "repro_test":
        if path.startswith("tests/") and any(expected.startswith("tests/") for expected in expected_paths):
            return True
        if path.endswith(".py") and _has_parent_overlap(path, expected_paths):
            return True

    return False


def _is_reasonable_metadata_omission(path: str, *, expected_paths: list[str], family: str) -> bool:
    if family == "terminal_workflow" and path in {"Makefile", "pyproject.toml"}:
        return True

    if not path.endswith(".py"):
        return False

    if _has_parent_overlap(path, expected_paths):
        return True

    expected_roots = {normalize_path(expected).split("/")[0] for expected in expected_paths if "/" in normalize_path(expected)}
    path_root = normalize_path(path).split("/")[0] if "/" in normalize_path(path) else ""
    return bool(path_root and path_root in expected_roots)


def classify_touched_path(
    path: str,
    *,
    expected_paths: list[str],
    family: str,
    task_type: str | None,
    allowed_support_files: list[str],
    allowed_support_globs: list[str],
) -> ClassifiedPath:
    normalized = normalize_path(path)
    if is_runtime_artifact_path(normalized):
        return ClassifiedPath(normalized, PATH_CLASS_IGNORED_RUNTIME_ARTIFACT, "runtime artifact pattern")
    if any(paths_equal(normalized, expected) for expected in expected_paths):
        return ClassifiedPath(normalized, PATH_CLASS_EXACT_EXPECTED, "explicitly listed in expected_files")
    if _is_task_adjacent_support(
        normalized,
        expected_paths=expected_paths,
        family=family,
        task_type=task_type,
        allowed_support_files=allowed_support_files,
        allowed_support_globs=allowed_support_globs,
    ):
        return ClassifiedPath(normalized, PATH_CLASS_TASK_ADJACENT_SUPPORT, "task/family support rule")
    if _is_reasonable_metadata_omission(normalized, expected_paths=expected_paths, family=family):
        return ClassifiedPath(normalized, PATH_CLASS_METADATA_OMISSION_REASONABLE, "task-related omission in expected_files metadata")
    return ClassifiedPath(normalized, PATH_CLASS_CLEARLY_UNRELATED, "meaningful edit outside task scope")


def enforce_path_policy(
    touched: list[str],
    allowlist: list[str],
    forbidden: list[str],
    *,
    expected_paths: list[str] | None = None,
    family: str = "",
    task_type: str | None = None,
    allowed_support_files: list[str] | None = None,
    allowed_support_globs: list[str] | None = None,
) -> PolicyCheckResult:
    expected_paths = [normalize_path(path) for path in (expected_paths or [])]
    allowed_support_files = [normalize_path(path) for path in (allowed_support_files or [])]
    allowed_support_globs = [normalize_path(path) for path in (allowed_support_globs or [])]
    normalized_touched = [normalize_path(path) for path in touched]

    classified = [
        classify_touched_path(
            path,
            expected_paths=expected_paths,
            family=family,
            task_type=task_type,
            allowed_support_files=allowed_support_files,
            allowed_support_globs=allowed_support_globs,
        )
        for path in normalized_touched
    ]

    ignored_paths = [item.path for item in classified if item.classification == PATH_CLASS_IGNORED_RUNTIME_ARTIFACT]
    meaningful = [item.path for item in classified if item.classification != PATH_CLASS_IGNORED_RUNTIME_ARTIFACT]

    allowlist_ok = all(any(_path_matches_scope(path, prefix) for prefix in allowlist) for path in meaningful)
    forbidden_hits = [path for path in meaningful if any(_path_matches_scope(path, prefix) for prefix in forbidden)]
    forbidden_ok = not forbidden_hits

    suspicious = [
        path
        for path in meaningful
        if path.endswith("conftest.py") or path.startswith(".github/") or path.startswith(".git/")
    ]

    meaningful_expected = [item.path for item in classified if item.classification == PATH_CLASS_EXACT_EXPECTED]
    meaningful_unexpected = [
        item.path
        for item in classified
        if item.classification
        in {PATH_CLASS_TASK_ADJACENT_SUPPORT, PATH_CLASS_METADATA_OMISSION_REASONABLE, PATH_CLASS_CLEARLY_UNRELATED}
    ]
    allowed_support = [item.path for item in classified if item.classification == PATH_CLASS_TASK_ADJACENT_SUPPORT]
    metadata_omission = [item.path for item in classified if item.classification == PATH_CLASS_METADATA_OMISSION_REASONABLE]

    violating_paths = [
        item.path
        for item in classified
        if item.classification == PATH_CLASS_CLEARLY_UNRELATED or item.path in forbidden_hits
    ]
    violating_paths = list(dict.fromkeys(violating_paths))

    forbidden_reason_detail = None
    if violating_paths:
        forbidden_reason_detail = f"clearly unrelated meaningful edits: {', '.join(violating_paths)}"

    return PolicyCheckResult(
        allowlist_ok=allowlist_ok,
        forbidden_ok=forbidden_ok,
        suspicious_patterns=suspicious,
        ignored_junk_paths=ignored_paths,
        normalized_touched_paths=normalized_touched,
        path_classifications={item.path: item.classification for item in classified},
        path_classification_reasons={item.path: item.reason for item in classified},
        meaningful_touched_paths=meaningful,
        meaningful_expected_paths=meaningful_expected,
        meaningful_unexpected_paths=meaningful_unexpected,
        allowed_support_paths=allowed_support,
        metadata_omission_paths=metadata_omission,
        violating_paths=violating_paths,
        forbidden_reason_detail=forbidden_reason_detail,
    )


def benchmark_asset_integrity(task_dir: Path) -> bool:
    return (task_dir / "task.yaml").exists() and (task_dir / "prompt.txt").exists() and (task_dir / "metadata.json").exists()
