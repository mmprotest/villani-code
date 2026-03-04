from __future__ import annotations

import fnmatch
import os
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
        target = self._target_for(tool, payload)
        for rule in self.config.deny:
            if self._matches(rule, tool, target):
                return Decision.DENY
        for rule in self.config.ask:
            if self._matches(rule, tool, target):
                return Decision.ASK
        for rule in self.config.allow:
            if self._matches(rule, tool, target):
                if auto_accept_edits and tool in {"Edit", "Write", "Patch"}:
                    return Decision.ALLOW
                return Decision.ALLOW
        if bypass:
            return Decision.ALLOW
        return Decision.ASK

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


def bash_matches(pattern: str, command: str) -> bool:
    # operator-aware token matching to avoid prefix exploits like `&& rm -rf /`.
    c_tokens = _safe_shell_split(command)
    if any(t in {"&&", "||", ";", "|", ">", ">>", "<", "2>", "2>>", "&", "|&"} for t in c_tokens):
        return False
    p_tokens = _safe_shell_split(pattern)
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


def _safe_shell_split(value: str) -> list[str]:
    primary_posix = os.name != "nt"
    for posix in (primary_posix, not primary_posix):
        try:
            return shlex.split(value, posix=posix)
        except ValueError:
            continue
    return _conservative_split(value)


def _conservative_split(value: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in value:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            current.append(ch)
            continue
        if ch in {'"', "'"}:
            if quote is None:
                quote = ch
            elif quote == ch:
                quote = None
            current.append(ch)
            continue
        if ch.isspace() and quote is None:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(ch)
    if escaped:
        current.append("\\")
    if current:
        tokens.append("".join(current))
    return tokens


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
