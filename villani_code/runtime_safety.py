from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence


CRITICAL_RUNTIME_DEPENDENCIES: tuple[str, ...] = ("pydantic", "yaml", "requests")


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_dependency_origin(module_name: str) -> Path | None:
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        loaded_path = getattr(loaded, "__file__", None)
        if loaded_path:
            return Path(loaded_path)

    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return None
    if spec.origin and spec.origin not in {"built-in", "frozen"}:
        return Path(spec.origin)
    if spec.submodule_search_locations:
        first = next(iter(spec.submodule_search_locations), None)
        if first:
            return Path(first)
    raise RuntimeError(
        f"Unable to determine filesystem origin for runtime dependency '{module_name}'."
    )


def ensure_runtime_dependencies_not_shadowed(
    repo: Path, dependencies: Sequence[str] = CRITICAL_RUNTIME_DEPENDENCIES
) -> None:
    resolved_repo = repo.resolve()
    for dependency in dependencies:
        origin = _resolve_dependency_origin(dependency)
        if origin is None:
            continue
        if _path_within(origin, resolved_repo):
            raise RuntimeError(
                "Target repository is shadowing Villani Code runtime dependencies. "
                f"Dependency '{dependency}' resolved to '{origin}', inside repo '{resolved_repo}'. "
                "Run Villani Code from outside the target repository, or rename/remove the conflicting package path."
            )


@contextmanager
def temporary_sys_path(prepend_paths: Sequence[Path | str]) -> Iterator[None]:
    original = list(sys.path)
    try:
        for path in reversed(prepend_paths):
            sys.path.insert(0, str(path))
        yield
    finally:
        sys.path[:] = original

