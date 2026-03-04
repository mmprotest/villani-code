from __future__ import annotations

import re
from urllib.parse import urlparse

_CHAINING_PATTERN = re.compile(r"(?:&&|\|\||;|\|&|\||\d?>>?|<|>)")
_URL_PATTERN = re.compile(r"https?://[^\s'\"]+")
_ESCAPED_SPACE = re.compile(r"\\\s")


def analyze_bash_command(command: str) -> list[str]:
    warnings: list[str] = []
    if _ESCAPED_SPACE.search(command):
        warnings.append("Command contains backslash-escaped whitespace that could alter command parsing")
    if _CHAINING_PATTERN.search(command):
        warnings.append("Command uses operator chaining (&&, |, ;, redirects) which increases risk")

    for url in _URL_PATTERN.findall(command):
        host = urlparse(url).hostname
        if host:
            warnings.append(f"Command contains URL domain: {host}")
            break

    return warnings
