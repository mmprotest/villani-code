from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CommandEnvironmentDiagnostics:
    sanitization_ran: bool
    path_entries_removed: int
    direct_path_variables_removed: tuple[str, ...]
    variables_flagged: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentCommandEnvironment:
    values: dict[str, str]
    diagnostics: CommandEnvironmentDiagnostics


def _normalized(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_within(path: Path, roots: Sequence[Path]) -> bool:
    normalized = _normalized(path)
    for root in roots:
        try:
            normalized.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def runner_private_roots(
    *,
    workspace: Path,
    internal_work_dir: Path | None = None,
    debug_root: Path | None = None,
    dependency_roots: Sequence[Path] = (),
) -> tuple[Path, ...]:
    """Return known Villani-owned roots without inspecting their contents."""
    package_root = Path(__file__).resolve().parent.parent
    candidates = [package_root, *dependency_roots]

    executable = _normalized(Path(sys.executable))
    runtime_prefix = _normalized(Path(sys.prefix))
    base_prefix = _normalized(Path(getattr(sys, "base_prefix", sys.prefix)))
    if runtime_prefix != base_prefix and _is_within(executable, (runtime_prefix,)):
        candidates.append(runtime_prefix)

    if internal_work_dir is not None:
        candidates.append(internal_work_dir)

    workspace_root = _normalized(workspace)
    if debug_root is not None and not _is_within(debug_root, (workspace_root,)):
        candidates.append(debug_root)

    roots: list[Path] = []
    for candidate in candidates:
        normalized = _normalized(candidate)
        if normalized not in roots:
            roots.append(normalized)
    return tuple(roots)


def _contains_private_absolute_path(value: str, roots: Sequence[Path]) -> bool:
    normalized_value = os.path.normcase(value)
    return any(os.path.normcase(str(root)) in normalized_value for root in roots)


def build_agent_command_environment(
    *,
    workspace: Path,
    source_environment: Mapping[str, str] | None = None,
    private_roots: Sequence[Path] | None = None,
    path_list_variables: Sequence[str] = ("PATH",),
    runner_variable_names: Sequence[str] = (),
) -> AgentCommandEnvironment:
    """Build an isolated child environment for commands issued by the agent."""
    environment = dict(os.environ if source_environment is None else source_environment)
    root_values = (
        runner_private_roots(workspace=workspace) if private_roots is None else private_roots
    )
    roots = tuple(_normalized(root) for root in root_values)
    path_variables = set(path_list_variables)
    runner_variables = set(runner_variable_names)
    runner_variables.update(name for name in environment if name.startswith("VILLANI_"))

    path_entries_removed = 0
    direct_removed: list[str] = []
    flagged: list[str] = []

    for name in list(environment):
        value = environment[name]
        if name in path_variables:
            kept: list[str] = []
            for entry in value.split(os.pathsep):
                if not entry:
                    continue
                if Path(entry).is_absolute() and _is_within(Path(entry), roots):
                    path_entries_removed += 1
                    continue
                kept.append(entry)
            environment[name] = os.pathsep.join(kept)
            continue

        if name in runner_variables:
            if _contains_private_absolute_path(value, roots) or name.startswith("VILLANI_"):
                environment.pop(name)
                direct_removed.append(name)
            continue

        value_path = Path(value)
        if value_path.is_absolute() and _is_within(value_path, roots):
            environment.pop(name)
            direct_removed.append(name)
        elif _contains_private_absolute_path(value, roots):
            flagged.append(name)

    return AgentCommandEnvironment(
        values=environment,
        diagnostics=CommandEnvironmentDiagnostics(
            sanitization_ran=True,
            path_entries_removed=path_entries_removed,
            direct_path_variables_removed=tuple(sorted(direct_removed)),
            variables_flagged=tuple(sorted(flagged)),
        ),
    )
