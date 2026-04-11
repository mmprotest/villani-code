from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable


class ShellFamily(str, Enum):
    POSIX = "posix"
    WINDOWS = "windows"


_BASH_ONLY_PATTERNS = ("$?", "tail -", "2>/dev/null", "&& echo", "; echo")

OSFamily = str
LockedShellFamily = str


@dataclass(slots=True)
class ShellEnvironment:
    os_family: OSFamily
    shell_family: LockedShellFamily
    shell_exe: str
    cwd: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class ShellCommandDecision:
    classification: str
    command: str
    offending_pattern: str = ""
    short_reason: str = ""
    suggested_equivalent: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {
            "classification": self.classification,
            "command": self.command,
        }
        if self.offending_pattern:
            payload["offending_pattern"] = self.offending_pattern
        if self.short_reason:
            payload["short_reason"] = self.short_reason
        if self.suggested_equivalent:
            payload["suggested_equivalent"] = self.suggested_equivalent
        return payload


def shell_family_for_platform(platform_name: str) -> ShellFamily:
    name = platform_name.lower()
    if name.startswith("win") or "powershell" in name or "cmd" in name:
        return ShellFamily.WINDOWS
    return ShellFamily.POSIX


def detect_shell_environment(cwd: str) -> ShellEnvironment:
    platform_name = os.sys.platform.lower()
    if platform_name.startswith("win"):
        os_family = "windows"
    elif platform_name == "darwin":
        os_family = "mac"
    else:
        os_family = "linux"

    shell_exe = os.environ.get("SHELL") or os.environ.get("COMSPEC") or os.environ.get("PSModulePath", "")
    shell_exe = str(shell_exe or "").strip()
    lowered_shell = shell_exe.lower()
    if "powershell" in lowered_shell or "pwsh" in lowered_shell:
        family = "powershell"
    elif lowered_shell.endswith("cmd.exe") or "\\cmd.exe" in lowered_shell:
        family = "cmd"
    elif lowered_shell.endswith("zsh") or "/zsh" in lowered_shell:
        family = "zsh"
    elif lowered_shell.endswith("bash") or "/bash" in lowered_shell:
        family = "bash"
    elif os_family == "windows":
        family = "cmd"
        if not shell_exe:
            shell_exe = "cmd.exe"
    else:
        family = "unknown"
    return ShellEnvironment(
        os_family=os_family,
        shell_family=family,
        shell_exe=shell_exe,
        cwd=str(cwd),
    )


def classify_and_rewrite_command(command: str, shell_family: str) -> ShellCommandDecision:
    raw = str(command or "").strip()
    if not raw:
        return ShellCommandDecision(classification="allowed", command=raw)

    if shell_family == "cmd":
        if _has_bash_heredoc(raw):
            return ShellCommandDecision(
                classification="blocked",
                command=raw,
                offending_pattern="<<EOF",
                short_reason="bash heredoc is not valid in cmd",
            )
        if re.search(r"\|\s*(ForEach-Object|Where-Object|Select-Object)\b", raw, re.IGNORECASE):
            return ShellCommandDecision(
                classification="blocked",
                command=raw,
                offending_pattern="| ForEach-Object",
                short_reason="powershell object pipeline syntax in cmd",
            )
        if "\n" in raw and re.search(r"\b(do|done|then|fi)\b", raw):
            return ShellCommandDecision(
                classification="blocked",
                command=raw,
                offending_pattern="bash multiline construct",
                short_reason="bash multiline construct is not valid in cmd",
            )
        rewritten = _rewrite_for_cmd(raw)
        if rewritten != raw:
            return ShellCommandDecision(classification="needs_rewrite", command=rewritten)
        return ShellCommandDecision(classification="allowed", command=raw)

    if shell_family == "powershell":
        if _has_bash_heredoc(raw):
            return ShellCommandDecision(
                classification="blocked",
                command=raw,
                offending_pattern="<<EOF",
                short_reason="bash heredoc is not valid in powershell",
            )
        rewritten = _rewrite_for_powershell(raw)
        if rewritten != raw:
            return ShellCommandDecision(classification="needs_rewrite", command=rewritten)
        return ShellCommandDecision(classification="allowed", command=raw)

    if shell_family in {"bash", "zsh"}:
        if re.match(r"^\s*(del\s+/q|findstr\b|dir\s+/b)\b", raw, re.IGNORECASE):
            return ShellCommandDecision(
                classification="blocked",
                command=raw,
                offending_pattern="cmd-only token",
                short_reason="cmd syntax is malformed for bash/zsh execution",
            )
        return ShellCommandDecision(classification="allowed", command=raw)

    return ShellCommandDecision(classification="allowed", command=raw)


def _has_bash_heredoc(command: str) -> bool:
    return bool(re.search(r"<<\s*'?EOF'?", command, re.IGNORECASE))


def _rewrite_for_cmd(command: str) -> str:
    rm_match = re.match(r"^\s*rm\s+(.+)$", command)
    if rm_match:
        return f"del /q {rm_match.group(1).strip()}"
    grep_match = re.match(r'^\s*grep\s+("([^"]+)"|\'([^\']+)\'|(\S+))\s+(.+)$', command)
    if grep_match:
        pattern = grep_match.group(2) or grep_match.group(3) or grep_match.group(4) or ""
        file_part = grep_match.group(5).strip()
        return f'findstr /n /c:"{pattern}" {file_part}'
    tail_match = re.match(r"^\s*tail\s+-n\s+(\d+)\s+(.+)$", command)
    if tail_match:
        num = tail_match.group(1)
        file_part = tail_match.group(2).strip().strip('"').strip("'")
        return f'powershell -NoProfile -Command "Get-Content \'{file_part}\' -Tail {num}"'
    return command


def _rewrite_for_powershell(command: str) -> str:
    grep_match = re.match(r'^\s*grep\s+("([^"]+)"|\'([^\']+)\'|(\S+))\s+(.+)$', command)
    if grep_match:
        pattern = grep_match.group(2) or grep_match.group(3) or grep_match.group(4) or ""
        file_part = grep_match.group(5).strip()
        return f'Select-String -Pattern "{pattern}" {file_part}'
    tail_match = re.match(r"^\s*tail\s+-n\s+(\d+)\s+(.+)$", command)
    if tail_match:
        num = tail_match.group(1)
        file_part = tail_match.group(2).strip()
        return f"Get-Content {file_part} -Tail {num}"
    return command


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
