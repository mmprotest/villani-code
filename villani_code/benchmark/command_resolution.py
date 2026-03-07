from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(slots=True)
class ResolvedCommand:
    argv: list[str]
    shell: bool
    display_command: str


def _split_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command, posix=os.name != "nt")
    return [str(token) for token in command]


def normalize_command_for_platform(command: str | Sequence[str]) -> ResolvedCommand:
    argv = _split_command(command)
    if not argv:
        return ResolvedCommand(argv=[], shell=False, display_command="")

    head = argv[0].lower()
    if head in {"python", "python3", "py"}:
        argv = [sys.executable, *argv[1:]]
    elif head == "pytest":
        argv = [sys.executable, "-m", "pytest", *argv[1:]]
    elif head == "pip":
        argv = [sys.executable, "-m", "pip", *argv[1:]]
    elif shutil.which(argv[0]) is None and "." not in argv[0] and "/" not in argv[0] and "\\" not in argv[0]:
        argv = [sys.executable, "-m", *argv]

    return ResolvedCommand(argv=argv, shell=False, display_command=shlex.join(argv))


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
