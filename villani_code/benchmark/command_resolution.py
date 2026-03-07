from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence


KNOWN_PYTHON_ALIASES = {"python", "python3", "py"}
KNOWN_MODULE_TOOLS = {"pytest", "pip"}


class CommandResolutionStatus(str, Enum):
    UNCHANGED = "unchanged"
    REWRITTEN = "rewritten"
    EMPTY = "empty"


@dataclass(slots=True)
class ResolvedCommand:
    argv: list[str]
    shell: bool
    display_command: str
    status: CommandResolutionStatus
    reason: str | None = None


@dataclass(slots=True)
class CommandResolutionResult:
    resolved: ResolvedCommand
    executable_found: bool


def _split_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command, posix=os.name != "nt")
    return [str(token) for token in command]


def resolve_command(command: str | Sequence[str]) -> CommandResolutionResult:
    argv = _split_command(command)
    if not argv:
        return CommandResolutionResult(
            resolved=ResolvedCommand(argv=[], shell=False, display_command="", status=CommandResolutionStatus.EMPTY, reason="empty_command"),
            executable_found=False,
        )

    original = list(argv)
    head = argv[0].lower()
    if head in KNOWN_PYTHON_ALIASES:
        argv = [sys.executable, *argv[1:]]
        reason = f"normalized interpreter alias '{original[0]}'"
    elif head in KNOWN_MODULE_TOOLS:
        argv = [sys.executable, "-m", head, *argv[1:]]
        reason = f"allowlisted module command '{head}'"
    else:
        reason = None

    status = CommandResolutionStatus.REWRITTEN if argv != original else CommandResolutionStatus.UNCHANGED
    executable_found = bool(shutil.which(argv[0])) or Path(argv[0]).exists() or argv[0] == sys.executable
    return CommandResolutionResult(
        resolved=ResolvedCommand(argv=argv, shell=False, display_command=shlex.join(argv), status=status, reason=reason),
        executable_found=executable_found,
    )


def normalize_command_for_platform(command: str | Sequence[str]) -> ResolvedCommand:
    return resolve_command(command).resolved


def run_normalized_command(command: str | Sequence[str], cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    resolved = normalize_command_for_platform(command)
    return subprocess.run(
        resolved.argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
