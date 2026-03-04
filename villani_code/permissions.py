from __future__ import annotations

import fnmatch
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PolicyDecision:
    decision: Decision
    reason: str


@dataclass
class PermissionRule:
    tool: str
    pattern: str

    @classmethod
    def parse(cls, raw: str) -> "PermissionRule":
        if "(" not in raw or not raw.endswith(")"):
            raise ValueError(f"Invalid rule format: {raw}")
        tool, body = raw.split("(", 1)
        return cls(tool=tool.strip(), pattern=body[:-1].strip())


@dataclass
class PermissionConfig:
    deny: list[PermissionRule]
    ask: list[PermissionRule]
    allow: list[PermissionRule]

    @classmethod
    def from_strings(cls, deny: Iterable[str], ask: Iterable[str], allow: Iterable[str]) -> "PermissionConfig":
        return cls(
            deny=[PermissionRule.parse(r) for r in deny],
            ask=[PermissionRule.parse(r) for r in ask],
            allow=[PermissionRule.parse(r) for r in allow],
        )


class PermissionEngine:
    def __init__(self, config: PermissionConfig, repo: Path):
        self.config = config
        self.repo = repo.resolve()

    def evaluate(self, tool: str, payload: dict, bypass: bool = False, auto_accept_edits: bool = False) -> Decision:
        return self.evaluate_with_reason(tool, payload, bypass=bypass, auto_accept_edits=auto_accept_edits).decision

    def evaluate_with_reason(self, tool: str, payload: dict, bypass: bool = False, auto_accept_edits: bool = False) -> PolicyDecision:
        target = self._target_for(tool, payload)
        for rule in self.config.deny:
            if self._matches(rule, tool, target):
                return PolicyDecision(Decision.DENY, f"Matched deny rule: {rule.tool}({rule.pattern})")
        for rule in self.config.ask:
            if self._matches(rule, tool, target):
                return PolicyDecision(Decision.ASK, f"Matched ask rule: {rule.tool}({rule.pattern})")
        for rule in self.config.allow:
            if self._matches(rule, tool, target):
                return PolicyDecision(Decision.ALLOW, f"Matched allow rule: {rule.tool}({rule.pattern})")

        if tool == "Bash" and self._is_bashsafe_enabled():
            safe = classify_bash_command(str(payload.get("command", "")))
            if safe.decision == Decision.ALLOW:
                return safe
            if safe.decision == Decision.DENY:
                return safe

        if bypass:
            return PolicyDecision(Decision.ALLOW, "Bypass flag enabled")

        if tool == "Bash":
            return PolicyDecision(Decision.ASK, "Bash defaults to ask unless explicitly allowlisted")
        return PolicyDecision(Decision.ASK, "Default ask policy")

    def _is_bashsafe_enabled(self) -> bool:
        return any(rule.tool == "BashSafe" for rule in self.config.allow)

    def _target_for(self, tool: str, payload: dict) -> str:
        if tool == "Bash":
            return str(payload.get("command", ""))
        if tool in {"Read", "Write", "Patch", "Edit"}:
            return str(payload.get("file_path", ""))
        if tool == "WebFetch":
            return str(payload.get("url", ""))
        return str(payload)

    def _matches(self, rule: PermissionRule, tool: str, target: str) -> bool:
        if rule.tool != tool:
            return False
        if tool == "Bash":
            return bash_matches(rule.pattern, target)
        if tool in {"Read", "Write", "Patch", "Edit"}:
            return path_matches(rule.pattern, target, self.repo)
        return fnmatch.fnmatch(target, rule.pattern)


@dataclass
class BashClassification:
    decision: Decision
    reason: str


_DISALLOWED_SHELL_TOKENS = {"&&", "||", ";", "|", "(", ")"}
_REDIRECTION_PREFIXES = (">", "<", "2>", "1>")
_SAFE_EXACT = {
    ("pwd",),
    ("ls",),
    ("dir",),
    ("cat",),
    ("type",),
    ("rg",),
    ("grep",),
    ("find",),
    ("pytest",),
    ("npm", "test"),
    ("pnpm", "test"),
    ("uv", "run", "pytest"),
    ("poetry", "run", "pytest"),
    ("python", "--version"),
    ("node", "--version"),
    ("git", "status"),
    ("git", "diff"),
    ("git", "log"),
    ("git", "show"),
}


_INSTALL_PREFIXES = {
    ("pip", "install"),
    ("python", "-m", "pip", "install"),
    ("uv", "pip", "install"),
    ("uv", "sync"),
    ("poetry", "add"),
    ("poetry", "install"),
    ("npm", "install"),
    ("pnpm", "install"),
}



def classify_bash_command(command: str) -> BashClassification:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return BashClassification(Decision.ASK, "Command parse error; requires approval")
    if not tokens:
        return BashClassification(Decision.ASK, "Empty command")

    if any(t in _DISALLOWED_SHELL_TOKENS for t in tokens):
        return BashClassification(Decision.ASK, "Shell chaining/subshell operators require approval")
    if any(t.startswith(_REDIRECTION_PREFIXES) for t in tokens):
        return BashClassification(Decision.ASK, "Redirection requires approval")
    if any("$(" in t or "`" in t for t in tokens):
        return BashClassification(Decision.ASK, "Subshell expansion requires approval")

    lowered = tuple(t.lower() for t in tokens)
    for pfx in _INSTALL_PREFIXES:
        if lowered[: len(pfx)] == pfx:
            return BashClassification(Decision.ASK, "Install command requires explicit approval")

    for exact in _SAFE_EXACT:
        if lowered[: len(exact)] == exact:
            return BashClassification(Decision.ALLOW, f"BashSafe allowlist matched: {' '.join(exact)}")

    return BashClassification(Decision.ASK, "Not in BashSafe allowlist")


def bash_matches(pattern: str, command: str) -> bool:
    # operator-aware token matching to avoid prefix exploits like `&& rm -rf /`.
    c_tokens = shlex.split(command)
    if any(t in {"&&", "||", ";", "|"} for t in c_tokens):
        return False
    p_tokens = shlex.split(pattern)
    if p_tokens == ["*"]:
        return True
    if p_tokens and p_tokens[-1] == "*":
        base = p_tokens[:-1]
        return c_tokens[: len(base)] == base and len(c_tokens) >= len(base)
    if len(p_tokens) != len(c_tokens):
        return False
    for p, c in zip(p_tokens, c_tokens):
        if p == "*":
            continue
        if p != c:
            return False
    return True


def path_matches(pattern: str, candidate: str, repo: Path) -> bool:
    cand = _resolve_pattern_path(candidate, repo)
    patt = pattern
    if patt.startswith("~/"):
        patt = str(Path.home() / patt[2:])
    elif patt.startswith("//"):
        patt = patt[1:]
    elif patt.startswith("/"):
        patt = str(repo / patt[1:])
    elif patt.startswith("./"):
        patt = str(repo / patt[2:])
    else:
        patt = str(repo / patt)
    return fnmatch.fnmatch(cand, patt)


def _resolve_pattern_path(raw: str, repo: Path) -> str:
    p = Path(raw)
    if not p.is_absolute():
        p = repo / p
    return str(p.resolve())
