from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(slots=True)
class TransformApplyResult:
    success: bool
    apply_mode: str = "none"
    new_content: str = ""
    diff_text: str = ""
    parse_failure_reason: str = ""
    apply_failure_reason: str = ""
    changed_line_count: int = 0


@dataclass(slots=True)
class PatchProposal:
    mode: Literal["full_file", "snippet_replace"]
    file_path: str
    new_content: str | None = None
    old_snippet: str | None = None
    new_snippet: str | None = None
    rationale: str | None = None


def apply_model_transform(
    *,
    workspace: Path,
    target_file: str,
    current_content: str,
    model_output: str,
    allow_additional_files: set[str] | None = None,
) -> TransformApplyResult:
    if not (model_output or "").strip():
        return TransformApplyResult(success=False, parse_failure_reason="empty_output")
    proposal, parse_failure_reason = _parse_patch_proposal(model_output)
    if proposal is None:
        return TransformApplyResult(success=False, parse_failure_reason=parse_failure_reason)

    allowed = {target_file}
    if allow_additional_files:
        allowed.update({p for p in allow_additional_files if p})
    if proposal.file_path not in allowed:
        return TransformApplyResult(success=False, parse_failure_reason="proposal_target_mismatch")

    if proposal.mode == "full_file":
        return _apply_full_file_proposal(
            workspace=workspace,
            target_file=target_file,
            current_content=current_content,
            proposal=proposal,
        )
    return _apply_snippet_replace_proposal(
        workspace=workspace,
        target_file=target_file,
        current_content=current_content,
        proposal=proposal,
    )


def _parse_patch_proposal(model_output: str) -> tuple[PatchProposal | None, str]:
    text = (model_output or "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, "proposal_json_decode_failed"
    if not isinstance(payload, dict):
        return None, "proposal_not_object"

    mode = payload.get("mode")
    file_path = payload.get("file_path")
    if mode not in {"full_file", "snippet_replace"}:
        return None, "proposal_invalid_mode"
    if not isinstance(file_path, str) or not file_path.strip():
        return None, "proposal_missing_file_path"

    proposal = PatchProposal(
        mode=mode,
        file_path=file_path.strip(),
        new_content=payload.get("new_content"),
        old_snippet=payload.get("old_snippet"),
        new_snippet=payload.get("new_snippet"),
        rationale=payload.get("rationale"),
    )
    if proposal.mode == "full_file":
        if not isinstance(proposal.new_content, str):
            return None, "proposal_missing_new_content"
    if proposal.mode == "snippet_replace":
        if not isinstance(proposal.old_snippet, str):
            return None, "proposal_missing_old_snippet"
        if not isinstance(proposal.new_snippet, str):
            return None, "proposal_missing_new_snippet"
    return proposal, ""


def _apply_full_file_proposal(*, workspace: Path, target_file: str, current_content: str, proposal: PatchProposal) -> TransformApplyResult:
    assert proposal.new_content is not None
    (workspace / target_file).write_text(proposal.new_content, encoding="utf-8")
    diff = _build_single_file_diff(target_file, current_content, proposal.new_content)
    return TransformApplyResult(
        success=True,
        apply_mode="full_file",
        new_content=proposal.new_content,
        diff_text=diff,
        changed_line_count=_count_changed_lines(current_content, proposal.new_content),
    )


def _apply_snippet_replace_proposal(*, workspace: Path, target_file: str, current_content: str, proposal: PatchProposal) -> TransformApplyResult:
    assert proposal.old_snippet is not None
    assert proposal.new_snippet is not None
    old_block = proposal.old_snippet
    new_block = proposal.new_snippet
    if old_block not in current_content:
        return TransformApplyResult(success=False, parse_failure_reason="proposal_old_snippet_not_found", apply_failure_reason="proposal_old_snippet_not_found")
    updated = current_content.replace(old_block, new_block, 1)
    (workspace / target_file).write_text(updated, encoding="utf-8")
    diff = _build_single_file_diff(target_file, current_content, updated)
    return TransformApplyResult(
        success=True,
        apply_mode="snippet_replace",
        new_content=updated,
        diff_text=diff,
        changed_line_count=_count_changed_lines(current_content, updated),
    )


def _build_single_file_diff(path: str, before: str, after: str) -> str:
    if before == after:
        return ""
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )


def _count_changed_lines(before: str, after: str) -> int:
    return sum(1 for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line.startswith("+ ") or line.startswith("- "))
