from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.path_authority import should_ignore_for_verification


def run_static_verification(repo_root: str, changed_files: list[str]) -> dict[str, Any]:
    repo = Path(repo_root)
    findings: list[str] = []
    meaningful = False
    syntax_errors = 0
    existing = 0
    skipped_internal = 0
    skipped_directories = 0
    skipped_unreadable = 0
    considered_files: list[str] = []
    for rel in changed_files:
        if should_ignore_for_verification(rel, repo_root=repo):
            skipped_internal += 1
            continue
        p = repo / rel
        if not p.exists():
            findings.append(f"missing_changed_file:{rel}")
            continue
        if p.is_dir():
            skipped_directories += 1
            findings.append(f"skipped_directory:{rel}")
            continue
        existing += 1
        considered_files.append(rel)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            skipped_unreadable += 1
            findings.append(f"unreadable_changed_file:{rel}")
            continue
        if text.strip():
            meaningful = True
        if "TODO" in text or "FIXME" in text:
            findings.append(f"todo_fixme:{rel}")
        if rel.endswith(".py"):
            try:
                compile(text, rel, "exec")
            except SyntaxError:
                syntax_errors += 1
                findings.append(f"syntax_error:{rel}")
    suspicious_breadth = len(considered_files) > 8
    if suspicious_breadth:
        findings.append(f"suspicious_breadth:{len(considered_files)}")
    return {
        "changed_files_exist": existing == len(considered_files),
        "meaningful_patch": meaningful and bool(considered_files),
        "suspicious_breadth": suspicious_breadth,
        "findings": findings,
        "syntax_errors": syntax_errors,
        "changed_count": len(considered_files),
        "skipped_internal": skipped_internal,
        "skipped_directories": skipped_directories,
        "skipped_unreadable": skipped_unreadable,
    }
