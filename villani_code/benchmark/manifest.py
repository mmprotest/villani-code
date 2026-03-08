from __future__ import annotations

import hashlib
from pathlib import Path


def command_set_checksum(commands: list[str]) -> str:
    h = hashlib.sha256()
    for command in commands:
        h.update(command.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def repo_checksum(repo: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts):
        rel = path.relative_to(repo).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()[:16]
