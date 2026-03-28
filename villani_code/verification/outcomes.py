from __future__ import annotations

from typing import Any

from villani_code.autonomy import TaskContract, contract_allows_edits, normalize_task_contract


def _has_useful_localization(localization: dict[str, Any]) -> bool:
    files = list(localization.get("target_files", []) or [])
    bug_class = str(localization.get("likely_bug_class", "")).strip()
    intent = str(localization.get("repair_intent", "")).strip()
    confidence = float(localization.get("confidence", 0.0) or 0.0)
    return bool(files) and bug_class not in {"", "unknown"} and bool(intent) and confidence >= 0.45


def classify_node_outcome(
    contract_type: str,
    static_result: dict[str, Any],
    command_results: list[dict[str, Any]],
    changed_files: list[str],
    prose_only: bool = False,
    localization: dict[str, Any] | None = None,
    prior_fingerprints: list[str] | None = None,
    previous_localization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = normalize_task_contract(contract_type)
    localization = localization or {}
    prior_fingerprints = prior_fingerprints or []
    previous_localization = previous_localization or {}

    command_fail = any(int(r.get("exit", 0)) != 0 for r in command_results)
    any_command = bool(command_results)
    repeated_in_run = any(bool(r.get("repeated_failure")) for r in command_results)
    failure_fingerprints = [str(r.get("failure_fingerprint", "")) for r in command_results if r.get("failure_fingerprint")]
    repeated_across_history = any(fp in prior_fingerprints for fp in failure_fingerprints)
    repeated_failure = repeated_in_run or repeated_across_history
    patch_exists = bool(changed_files)
    meaningful_patch = bool(static_result.get("meaningful_patch"))
    suspicious_breadth = bool(static_result.get("suspicious_breadth"))
    has_localization = _has_useful_localization(localization)
    prev_conf = float(previous_localization.get("confidence", 0.0) or 0.0)
    loc_conf = float(localization.get("confidence", 0.0) or 0.0)
    localization_stale = bool(localization) and loc_conf <= prev_conf and list(localization.get("target_files", [])) == list(previous_localization.get("target_files", []))

    status = "partial"
    reason = "insufficient evidence"

    if prose_only:
        status, reason = "stale", "runner produced prose-only output"
    elif contract == TaskContract.LOCALIZE:
        if has_localization:
            status, reason = "passed", "useful localization evidence produced"
        elif localization:
            status, reason = "partial", "localization evidence weak"
        else:
            status, reason = "failed", "no localization evidence produced"
    elif contract == TaskContract.INSPECT:
        if has_localization or bool(static_result.get("findings")) or any_command:
            status, reason = "passed", "inspection produced actionable evidence"
        else:
            status, reason = "failed", "inspection lacked concrete repo evidence"
    elif contract == TaskContract.REPRODUCE:
        if any_command and command_fail:
            status, reason = "passed", "failure reproduced"
        elif any_command and not command_fail:
            status, reason = "partial", "commands ran but did not reproduce failure"
        else:
            status, reason = "failed", "no reproduction evidence"
    elif contract in {TaskContract.NARROW_FIX, TaskContract.BROAD_FIX, TaskContract.IMPLEMENT, TaskContract.CONTAIN_CHANGE, TaskContract.CLEANUP}:
        if not patch_exists:
            status, reason = "failed", "no patch produced"
        elif not meaningful_patch:
            status, reason = "failed", "patch not meaningful"
        elif any_command and not command_fail:
            status, reason = "passed", "patch validated successfully"
        elif not any_command:
            status, reason = "partial", "patch created without validation"
        else:
            status, reason = "failed", "patch failed validation"
    elif contract == TaskContract.VALIDATE:
        if not any_command:
            status, reason = "failed", "validate node ran no commands"
        elif command_fail:
            status, reason = "failed", "validation commands failed"
        else:
            status, reason = "passed", "validation commands passed"
    elif contract == TaskContract.SUMMARIZE:
        if patch_exists:
            status, reason = "failed", "summarize node edited files"
        elif prose_only:
            status, reason = "partial", "summary too thin"
        else:
            status, reason = "passed", "summary completed without edits"
    elif not contract_allows_edits(contract):
        status, reason = ("passed", "non-edit contract satisfied") if (any_command or static_result.get("findings") == []) else ("partial", "non-edit node incomplete")

    validation_worsened = command_fail and patch_exists
    patch_no_improvement = patch_exists and (not any_command or command_fail)

    return {
        "status": status,
        "reason": reason,
        "patch_exists": patch_exists,
        "meaningful_patch": meaningful_patch,
        "same_failure_repeated": repeated_failure,
        "validation_worsened": validation_worsened,
        "suspicious_breadth": suspicious_breadth,
        "patch_no_improvement": patch_no_improvement,
        "tool_denied": False,
        "prose_only": prose_only,
        "changed_files": list(changed_files),
        "failure_fingerprints": failure_fingerprints,
        "localization_weak": bool(localization) and not has_localization,
        "localization_stale": localization_stale,
    }
