from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from villani_code.runtime.proposal_tools import ProposalToolCall


@dataclass(slots=True)
class ProposalApplyResult:
    success: bool
    apply_mode: str = "none"
    new_content_by_file: dict[str, str] | None = None
    diff_text: str = ""
    validation_error: str = ""
    apply_error: str = ""
    meaningful_change: bool = False
    changed_line_count: int = 0


def apply_proposal_tool_call(
    *,
    workspace: Path,
    proposal: ProposalToolCall,
    target_file: str,
    allowed_files: set[str],
) -> ProposalApplyResult:
    if proposal.name == "propose_full_file_rewrite":
        return _apply_full_file_rewrite(workspace=workspace, arguments=proposal.arguments, allowed_files=allowed_files)
    if proposal.name == "propose_snippet_replace":
        return _apply_snippet_replace(workspace=workspace, arguments=proposal.arguments, allowed_files=allowed_files)
    if proposal.name == "propose_two_file_rewrite":
        if target_file not in allowed_files:
            return ProposalApplyResult(success=False, validation_error="proposal_target_mismatch")
        return _apply_two_file_rewrite(workspace=workspace, arguments=proposal.arguments, allowed_files=allowed_files)
    return ProposalApplyResult(success=False, validation_error="invalid_tool_name")


def _apply_full_file_rewrite(*, workspace: Path, arguments: dict[str, Any], allowed_files: set[str]) -> ProposalApplyResult:
    file_path = arguments.get("file_path")
    new_content = arguments.get("new_content")
    if not isinstance(file_path, str) or not file_path.strip():
        return ProposalApplyResult(success=False, validation_error="proposal_missing_file_path")
    if not isinstance(new_content, str):
        return ProposalApplyResult(success=False, validation_error="proposal_missing_new_content")
    file_path = file_path.strip()
    if file_path not in allowed_files:
        return ProposalApplyResult(success=False, validation_error="proposal_target_mismatch")

    before = _read_file(workspace, file_path)
    (workspace / file_path).write_text(new_content, encoding="utf-8")
    diff = _single_diff(file_path, before, new_content)
    return ProposalApplyResult(
        success=True,
        apply_mode="full_file",
        new_content_by_file={file_path: new_content},
        diff_text=diff,
        meaningful_change=before != new_content,
        changed_line_count=_count_changed_lines(before, new_content),
    )


def _apply_snippet_replace(*, workspace: Path, arguments: dict[str, Any], allowed_files: set[str]) -> ProposalApplyResult:
    file_path = arguments.get("file_path")
    old_snippet = arguments.get("old_snippet")
    new_snippet = arguments.get("new_snippet")
    if not isinstance(file_path, str) or not file_path.strip():
        return ProposalApplyResult(success=False, validation_error="proposal_missing_file_path")
    if not isinstance(old_snippet, str):
        return ProposalApplyResult(success=False, validation_error="proposal_missing_old_snippet")
    if not isinstance(new_snippet, str):
        return ProposalApplyResult(success=False, validation_error="proposal_missing_new_snippet")
    file_path = file_path.strip()
    if file_path not in allowed_files:
        return ProposalApplyResult(success=False, validation_error="proposal_target_mismatch")

    before = _read_file(workspace, file_path)
    if old_snippet not in before:
        return ProposalApplyResult(success=False, apply_error="proposal_old_snippet_not_found")
    after = before.replace(old_snippet, new_snippet, 1)
    (workspace / file_path).write_text(after, encoding="utf-8")
    diff = _single_diff(file_path, before, after)
    return ProposalApplyResult(
        success=True,
        apply_mode="snippet_replace",
        new_content_by_file={file_path: after},
        diff_text=diff,
        meaningful_change=before != after,
        changed_line_count=_count_changed_lines(before, after),
    )


def _apply_two_file_rewrite(*, workspace: Path, arguments: dict[str, Any], allowed_files: set[str]) -> ProposalApplyResult:
    p1 = arguments.get("primary_file_path")
    c1 = arguments.get("primary_new_content")
    p2 = arguments.get("secondary_file_path")
    c2 = arguments.get("secondary_new_content")
    if not isinstance(p1, str) or not isinstance(p2, str):
        return ProposalApplyResult(success=False, validation_error="proposal_missing_file_path")
    if not isinstance(c1, str) or not isinstance(c2, str):
        return ProposalApplyResult(success=False, validation_error="proposal_missing_new_content")
    p1 = p1.strip(); p2 = p2.strip()
    if p1 not in allowed_files or p2 not in allowed_files or p1 == p2:
        return ProposalApplyResult(success=False, validation_error="proposal_target_mismatch")

    before1 = _read_file(workspace, p1)
    before2 = _read_file(workspace, p2)
    (workspace / p1).write_text(c1, encoding="utf-8")
    (workspace / p2).write_text(c2, encoding="utf-8")
    diff = "\n".join(filter(None, [_single_diff(p1, before1, c1), _single_diff(p2, before2, c2)]))
    changed = int(before1 != c1) + int(before2 != c2)
    return ProposalApplyResult(
        success=True,
        apply_mode="two_file_rewrite",
        new_content_by_file={p1: c1, p2: c2},
        diff_text=diff,
        meaningful_change=changed > 0,
        changed_line_count=_count_changed_lines(before1, c1) + _count_changed_lines(before2, c2),
    )


def _read_file(workspace: Path, rel_path: str) -> str:
    p = workspace / rel_path
    if not p.exists() or not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def _single_diff(path: str, before: str, after: str) -> str:
    if before == after:
        return ""
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))


def _count_changed_lines(before: str, after: str) -> int:
    return sum(1 for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line.startswith('+ ') or line.startswith('- '))
