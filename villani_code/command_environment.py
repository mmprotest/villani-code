from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


_CONVENTIONAL_EXECUTABLE_DIRS = {"bin", "sbin", "scripts"}
_RUNTIME_PATH_MARKERS = {
    ".venv",
    "deps",
    "dependencies",
    "dist-packages",
    "node_modules",
    "packages",
    "runner",
    "runtime",
    "site-packages",
    "venv",
    "villani",
}
_RUNNER_ENVIRONMENT_NAMES = {
    "RUN_CLAUDE_CODE_SMOKE",
}
_ABSOLUTE_PATH_TOKEN = re.compile(r"(?<![\w.-])(?:[A-Za-z]:[\\/]|/)[^\s,;]+")


@dataclass(frozen=True, slots=True)
class CommandEnvironmentDiagnostics:
    sanitization_ran: bool
    discovered_private_roots: tuple[str, ...]
    environment_variables_removed: tuple[str, ...]
    path_entries_removed: int
    runner_owned_variables_considered: tuple[str, ...]
    possible_private_path_variables_flagged: tuple[str, ...]

    @property
    def direct_path_variables_removed(self) -> tuple[str, ...]:
        return self.environment_variables_removed

    @property
    def variables_flagged(self) -> tuple[str, ...]:
        return self.possible_private_path_variables_flagged


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


def is_runner_owned_environment_variable(
    name: str,
    *,
    configured_names: Sequence[str] = (),
) -> bool:
    """Return whether *name* belongs to Villani's launch/runtime configuration."""
    return (
        name.startswith(("VILLANI_", "RUNNER_"))
        or name in _RUNNER_ENVIRONMENT_NAMES
        or name in configured_names
    )


def _looks_like_runtime_path(path: Path) -> bool:
    for part in path.parts:
        normalized = part.casefold()
        if normalized in _RUNTIME_PATH_MARKERS:
            return True
        if any(marker in normalized for marker in ("villani", "runner", "runtime")):
            return True
    return False


def _root_for_absolute_path(path: Path) -> Path:
    normalized = _normalized(path)
    if normalized.parent.name.casefold() in _CONVENTIONAL_EXECUTABLE_DIRS:
        return normalized.parent.parent
    if normalized.name.casefold() in _CONVENTIONAL_EXECUTABLE_DIRS:
        return normalized.parent
    if normalized.exists() and normalized.is_file():
        return normalized.parent
    if normalized.suffix:
        return normalized.parent
    return normalized


def _root_for_executable(path: Path) -> Path:
    normalized = _normalized(path)
    if normalized.parent.name.casefold() in _CONVENTIONAL_EXECUTABLE_DIRS:
        return normalized.parent.parent
    return normalized.parent


def _absolute_path_tokens(value: str) -> tuple[Path, ...]:
    tokens: list[Path] = []
    for match in _ABSOLUTE_PATH_TOKEN.finditer(value):
        raw = match.group(0).strip("\"'()[]{}")
        candidate = Path(raw)
        if candidate.is_absolute():
            tokens.append(candidate)
    return tuple(tokens)


def _roots_from_runner_environment_value(name: str, value: str) -> tuple[Path, ...]:
    candidates: list[Path] = []
    direct = Path(value)
    if direct.is_absolute() and os.pathsep not in value:
        executable_variable = name.endswith(("_BINARY", "_CLI", "_COMMAND", "_EXECUTABLE", "_TOOL"))
        candidates.append(
            _root_for_executable(direct)
            if executable_variable
            else _root_for_absolute_path(direct)
        )

    if os.pathsep in value:
        for entry in value.split(os.pathsep):
            candidate = Path(entry.strip())
            if candidate.is_absolute() and _looks_like_runtime_path(candidate):
                candidates.append(_root_for_absolute_path(candidate))

    for token in _absolute_path_tokens(value):
        if _looks_like_runtime_path(token):
            candidates.append(_root_for_absolute_path(token))

    roots: list[Path] = []
    for candidate in candidates:
        normalized = _normalized(candidate)
        if normalized not in roots:
            roots.append(normalized)
    return tuple(roots)


def runner_private_roots(
    *,
    workspace: Path,
    source_environment: Mapping[str, str] | None = None,
    internal_work_dir: Path | None = None,
    debug_root: Path | None = None,
    artifact_root: Path | None = None,
    install_root: Path | None = None,
    runtime_root: Path | None = None,
    dependency_roots: Sequence[Path] = (),
    runner_variable_names: Sequence[str] = (),
) -> tuple[Path, ...]:
    """Return Villani-owned roots derived from runtime state without scanning them."""
    environment = os.environ if source_environment is None else source_environment
    package_root = Path(__file__).resolve().parent.parent
    candidates = [package_root, install_root, runtime_root, *dependency_roots]

    executable = _normalized(Path(sys.executable))
    runtime_prefix = _normalized(Path(sys.prefix))
    base_prefix = _normalized(Path(getattr(sys, "base_prefix", sys.prefix)))
    if runtime_prefix != base_prefix:
        candidates.append(runtime_prefix)
    elif _looks_like_runtime_path(executable):
        candidates.append(_root_for_executable(executable))

    argv_executable = Path(sys.argv[0]) if sys.argv else Path()
    resolved_argv_executable = (
        Path(shutil.which(str(argv_executable)) or argv_executable)
        if str(argv_executable)
        else Path()
    )
    if resolved_argv_executable.is_absolute() and (
        "villani" in resolved_argv_executable.name.casefold()
        or _looks_like_runtime_path(resolved_argv_executable)
    ):
        candidates.append(_root_for_executable(resolved_argv_executable))

    candidates.extend((internal_work_dir, debug_root, artifact_root))
    for name, value in environment.items():
        if is_runner_owned_environment_variable(name, configured_names=runner_variable_names):
            candidates.extend(_roots_from_runner_environment_value(name, value))

    workspace_root = _normalized(workspace)
    roots: list[Path] = []
    for candidate in candidates:
        if candidate is None:
            continue
        normalized = _normalized(candidate)
        if _is_within(normalized, (workspace_root,)):
            continue
        if normalized not in roots:
            roots.append(normalized)
    return tuple(roots)


def _contains_private_absolute_path(value: str, roots: Sequence[Path]) -> bool:
    return any(_is_within(path, roots) for path in _absolute_path_tokens(value))


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
    discovered_roots = runner_private_roots(
        workspace=workspace,
        source_environment=environment,
        runner_variable_names=runner_variable_names,
    )
    root_values = [*discovered_roots, *(private_roots or ())]
    roots: list[Path] = []
    for root in root_values:
        normalized = _normalized(root)
        if normalized not in roots:
            roots.append(normalized)
    root_tuple = tuple(roots)
    path_variables = set(path_list_variables)
    runner_variables = {
        name
        for name in environment
        if is_runner_owned_environment_variable(name, configured_names=runner_variable_names)
    }

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
                if Path(entry).is_absolute() and _is_within(Path(entry), root_tuple):
                    path_entries_removed += 1
                    continue
                kept.append(entry)
            environment[name] = os.pathsep.join(kept)
            continue

        value_path = Path(value)
        if value_path.is_absolute() and _is_within(value_path, root_tuple):
            environment.pop(name)
            direct_removed.append(name)
        elif _contains_private_absolute_path(value, root_tuple):
            flagged.append(name)

    return AgentCommandEnvironment(
        values=environment,
        diagnostics=CommandEnvironmentDiagnostics(
            sanitization_ran=True,
            discovered_private_roots=tuple(str(root) for root in root_tuple),
            environment_variables_removed=tuple(sorted(direct_removed)),
            path_entries_removed=path_entries_removed,
            runner_owned_variables_considered=tuple(sorted(runner_variables)),
            possible_private_path_variables_flagged=tuple(sorted(flagged)),
        ),
    )
