from __future__ import annotations

from pathlib import Path
from typing import Any


def run_static_verification(repo_root: str, changed_files: list[str]) -> dict[str, Any]:
    repo = Path(repo_root)
    findings: list[str] = []
    meaningful = False
    for rel in changed_files:
        p = repo / rel
        if not p.exists():
            findings.append(f"missing_changed_file:{rel}")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            meaningful = True
        if "TODO" in text or "FIXME" in text:
            findings.append(f"todo_fixme:{rel}")
        if rel.endswith(".py"):
            try:
                compile(text, rel, "exec")
            except SyntaxError:
                findings.append(f"syntax_error:{rel}")
    suspicious_breadth = len(changed_files) > 8
    if suspicious_breadth:
        findings.append(f"suspicious_breadth:{len(changed_files)}")
    return {
        "changed_files_exist": all((repo / rel).exists() for rel in changed_files),
        "meaningful_patch": meaningful and bool(changed_files),
        "suspicious_breadth": suspicious_breadth,
        "findings": findings,
    }
