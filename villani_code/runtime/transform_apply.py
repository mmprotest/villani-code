from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from villani_code.patch_apply import PatchApplyError, apply_unified_diff, parse_unified_diff


@dataclass(slots=True)
class TransformApplyResult:
    success: bool
    apply_mode: str = "none"
    new_content: str = ""
    diff_text: str = ""
    parse_failure_reason: str = ""
    apply_failure_reason: str = ""
    changed_line_count: int = 0


def apply_model_transform(
    *,
    workspace: Path,
    target_file: str,
    current_content: str,
    model_output: str,
    allow_additional_files: set[str] | None = None,
) -> TransformApplyResult:
    text = (model_output or "").strip()
    if not text:
        return TransformApplyResult(success=False, parse_failure_reason="empty_output")

    allowed = {target_file}
    if allow_additional_files:
        allowed.update({p for p in allow_additional_files if p})

    unified = _try_apply_unified_diff(
        workspace=workspace,
        target_file=target_file,
        current_content=current_content,
        model_output=text,
        allowed_files=allowed,
    )
    if unified.success:
        return unified

    full_file = _try_apply_full_file(
        workspace=workspace,
        target_file=target_file,
        current_content=current_content,
        model_output=text,
    )
    if full_file.success:
        return full_file

    snippet = _try_apply_snippet_replace(
        workspace=workspace,
        target_file=target_file,
        current_content=current_content,
        model_output=text,
    )
    if snippet.success:
        return snippet

    parse_reason = snippet.parse_failure_reason or full_file.parse_failure_reason or unified.parse_failure_reason or "unrecognized_output"
    apply_reason = snippet.apply_failure_reason or full_file.apply_failure_reason or unified.apply_failure_reason
    return TransformApplyResult(success=False, parse_failure_reason=parse_reason, apply_failure_reason=apply_reason)


def _try_apply_unified_diff(*, workspace: Path, target_file: str, current_content: str, model_output: str, allowed_files: set[str]) -> TransformApplyResult:
    if "--- " not in model_output or "+++ " not in model_output:
        return TransformApplyResult(success=False, parse_failure_reason="unified_diff_not_detected")
    try:
        parsed = parse_unified_diff(model_output)
    except Exception as exc:  # noqa: BLE001
        return TransformApplyResult(success=False, parse_failure_reason="unified_diff_parse_failed", apply_failure_reason=str(exc))
    if not parsed:
        return TransformApplyResult(success=False, parse_failure_reason="unified_diff_parse_failed", apply_failure_reason="empty_diff")
    targets = {p.new_path.removeprefix("a/").removeprefix("b/") for p in parsed}
    if not targets.issubset(allowed_files):
        return TransformApplyResult(success=False, parse_failure_reason="unified_diff_target_mismatch")
    try:
        apply_unified_diff(workspace, model_output)
    except Exception as exc:  # noqa: BLE001
        return TransformApplyResult(success=False, parse_failure_reason="unified_diff_apply_failed", apply_failure_reason=str(exc))
    new_content = (workspace / target_file).read_text(encoding="utf-8") if (workspace / target_file).exists() else ""
    diff = _build_single_file_diff(target_file, current_content, new_content)
    return TransformApplyResult(
        success=True,
        apply_mode="unified_diff",
        new_content=new_content,
        diff_text=diff,
        changed_line_count=_count_changed_lines(current_content, new_content),
    )


def _try_apply_full_file(*, workspace: Path, target_file: str, current_content: str, model_output: str) -> TransformApplyResult:
    body = ""
    if model_output.startswith("NEW FILE CONTENT"):
        header, _, rest = model_output.partition("\n")
        hinted = header.replace("NEW FILE CONTENT", "").strip()
        if hinted and hinted != target_file:
            return TransformApplyResult(success=False, parse_failure_reason="full_file_target_mismatch")
        body = rest
    elif "--- " not in model_output and "+++ " not in model_output and "SNIPPET_REPLACE" not in model_output:
        body = model_output
    else:
        return TransformApplyResult(success=False, parse_failure_reason="full_file_not_detected")

    (workspace / target_file).write_text(body, encoding="utf-8")
    diff = _build_single_file_diff(target_file, current_content, body)
    return TransformApplyResult(
        success=True,
        apply_mode="full_file",
        new_content=body,
        diff_text=diff,
        changed_line_count=_count_changed_lines(current_content, body),
    )


def _try_apply_snippet_replace(*, workspace: Path, target_file: str, current_content: str, model_output: str) -> TransformApplyResult:
    if "SNIPPET_REPLACE" not in model_output or "OLD_SNIPPET:" not in model_output or "NEW_SNIPPET:" not in model_output:
        return TransformApplyResult(success=False, parse_failure_reason="snippet_replace_not_detected")

    file_hint = ""
    if "FILE:" in model_output:
        part = model_output.split("FILE:", 1)[1]
        file_hint = part.splitlines()[0].strip()
        if file_hint and file_hint != target_file:
            return TransformApplyResult(success=False, parse_failure_reason="snippet_target_mismatch")

    old_block = model_output.split("OLD_SNIPPET:", 1)[1].split("NEW_SNIPPET:", 1)[0].strip("\n")
    new_block = model_output.split("NEW_SNIPPET:", 1)[1].strip("\n")
    if old_block not in current_content:
        return TransformApplyResult(success=False, parse_failure_reason="snippet_old_block_not_found", apply_failure_reason="snippet_old_block_not_found")
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
