from __future__ import annotations

from enum import Enum
import os
from pathlib import Path
import shutil
import subprocess
from typing import Iterable


class ShellFamily(str, Enum):
    POSIX = "posix"
    WINDOWS = "windows"


_BASH_ONLY_PATTERNS = ("$?", "tail -", "2>/dev/null", "&& echo", "; echo")


def shell_family_for_platform(platform_name: str) -> ShellFamily:
    name = platform_name.lower()
    if name.startswith("win") or "powershell" in name or "cmd" in name:
        return ShellFamily.WINDOWS
    return ShellFamily.POSIX


def normalize_command_for_shell(command: str, family: ShellFamily) -> str:
    normalized = command.strip()
    if family == ShellFamily.WINDOWS:
        for pattern in _BASH_ONLY_PATTERNS:
            normalized = normalized.replace(pattern, "")
    return " ".join(normalized.split())


def supports_pipeline_tail(family: ShellFamily) -> bool:
    return family == ShellFamily.POSIX


def classify_shell_portability_failure(commands: Iterable[str]) -> bool:
    return any(any(p in cmd for p in _BASH_ONLY_PATTERNS) for cmd in commands)


def baseline_import_validation_command(family: ShellFamily) -> str:
    if family == ShellFamily.WINDOWS:
        return 'python -c "import villani_code"'
    return "python -c 'import villani_code'"


def shell_command_for_platform(
    command: str,
    *,
    platform_name: str | None = None,
) -> list[str]:
    family = shell_family_for_platform(platform_name or os.sys.platform)
    normalized = normalize_command_for_shell(command, family)
    if family == ShellFamily.WINDOWS:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            return [powershell, "-NoLogo", "-NoProfile", "-Command", normalized]
        return ["cmd.exe", "/d", "/s", "/c", normalized]
    bash = shutil.which("bash")
    if bash:
        return ["bash", "-lc", normalized]
    return ["sh", "-lc", normalized]


def run_portable_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        shell_command_for_platform(command, platform_name=os.sys.platform),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
