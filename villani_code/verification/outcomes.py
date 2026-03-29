from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from villani_code.autonomy import TaskContract, contract_allows_edits, normalize_task_contract
from villani_code.mission import DeltaClassification

INTERNAL_ARTIFACT_PREFIXES = (".villani/", ".villani_code/")


def _is_internal_artifact(path: str) -> bool:
    return str(path).startswith(INTERNAL_ARTIFACT_PREFIXES)


def _is_docs_only_path(path: str) -> bool:
    low = str(path).lower()
    if low.startswith("docs/"):
        return True
    return low.endswith((".md", ".rst", ".txt")) or low.endswith("/readme")


@dataclass(slots=True)
class VerificationBaseline:
    changed_files: list[str] = field(default_factory=list)
    validation_summary: dict[str, Any] = field(default_factory=dict)
    failure_fingerprints: list[str] = field(default_factory=list)
    localization: dict[str, Any] = field(default_factory=dict)
    execution_snapshot: dict[str, Any] = field(default_factory=dict)
    previous_command_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class VerificationDelta:
    classification: DeltaClassification = DeltaClassification.AMBIGUOUS
    score: float = 0.0
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _has_useful_localization(localization: dict[str, Any]) -> bool:
    files = list(localization.get("target_files", []) or [])
    bug_class = str(localization.get("likely_bug_class", "")).strip()
    intent = str(localization.get("repair_intent", "")).strip()
    confidence = float(localization.get("confidence", 0.0) or 0.0)
    return bool(files) and bug_class not in {"", "unknown"} and bool(intent) and confidence >= 0.45


def _validation_delta(baseline: VerificationBaseline, current_summary: dict[str, Any]) -> int:
    prior_failed = int((baseline.validation_summary or {}).get("failed", 0) or 0)
    now_failed = int((current_summary or {}).get("failed", 0) or 0)
    prior_passed = int((baseline.validation_summary or {}).get("passed", 0) or 0)
    now_passed = int((current_summary or {}).get("passed", 0) or 0)
    return (prior_failed - now_failed) + (now_passed - prior_passed)


def _classify_delta(
    contract: TaskContract,
    patch_exists: bool,
    meaningful_patch: bool,
    localization: dict[str, Any],
    previous_localization: dict[str, Any],
    baseline: VerificationBaseline,
    validation_summary: dict[str, Any],
    failure_fingerprints: list[str],
    suspicious_breadth: bool,
    execution_payload: dict[str, Any],
    validation_delta: dict[str, Any],
) -> VerificationDelta:
    score = 0.0
    reasons: list[str] = []
    val_delta = _validation_delta(baseline, validation_summary)
    prior_fps = set(baseline.failure_fingerprints)
    new_fps = [fp for fp in failure_fingerprints if fp and fp not in prior_fps]
    prior_activity = dict((baseline.execution_snapshot or {}).get("model_activity", {}) or {})
    current_activity = dict((execution_payload or {}).get("model_activity", {}) or {})
    prior_tool_errors = int(
        (prior_activity.get("tool_errors", (baseline.execution_snapshot or {}).get("tool_errors", 0)) or 0)
    )
    current_tool_errors = int(
        (current_activity.get("tool_errors", (execution_payload or {}).get("tool_errors", 0)) or 0)
    )
    tool_error_delta = prior_tool_errors - current_tool_errors

    has_localization = _has_useful_localization(localization)
    prev_conf = float(previous_localization.get("confidence", 0.0) or 0.0)
    loc_conf = float(localization.get("confidence", 0.0) or 0.0)
    prev_targets = list(previous_localization.get("target_files", []) or [])
    cur_targets = list(localization.get("target_files", []) or [])
    sharper_localization = has_localization and (loc_conf > prev_conf or (cur_targets and len(cur_targets) < len(prev_targets) and set(cur_targets).issubset(set(prev_targets))))

    if val_delta > 0:
        score += min(2.0, 0.7 + (0.4 * val_delta))
        reasons.append(f"validation_improved:{val_delta}")
    elif val_delta < 0:
        score -= min(2.0, 0.7 + (0.4 * abs(val_delta)))
        reasons.append(f"validation_regressed:{val_delta}")

    if int(validation_delta.get("failed_delta", 0) or 0) > 0:
        score += 0.35
        reasons.append("validation_failed_count_down")
    elif int(validation_delta.get("failed_delta", 0) or 0) < 0:
        score -= 0.35
        reasons.append("validation_failed_count_up")

    if patch_exists and meaningful_patch:
        score += 0.7
        reasons.append("meaningful_patch")
    elif patch_exists:
        score += 0.2
        reasons.append("weak_patch")

    if suspicious_breadth:
        score -= 0.5
        reasons.append("suspicious_breadth")

    if new_fps:
        score += 0.2
        reasons.append("failure_fingerprint_shift")
    if tool_error_delta > 0:
        score += 0.25
        reasons.append("tool_failures_reduced")
    elif tool_error_delta < 0:
        score -= 0.25
        reasons.append("tool_failures_increased")

    if contract == TaskContract.LOCALIZE:
        if sharper_localization:
            score += 1.0
            reasons.append("localization_sharpened")
        elif has_localization:
            score += 0.3
            reasons.append("localization_present")
        else:
            score -= 0.7
            reasons.append("localization_weak")
    elif contract == TaskContract.REPRODUCE:
        if validation_summary.get("any_failed"):
            score += 1.0
            reasons.append("failure_reproduced")
        else:
            score -= 0.4
            reasons.append("no_reproduction")
    elif contract in {TaskContract.NARROW_FIX, TaskContract.BROAD_FIX, TaskContract.IMPLEMENT, TaskContract.CONTAIN_CHANGE, TaskContract.CLEANUP}:
        if not patch_exists:
            score -= 1.0
            reasons.append("no_patch")
        if patch_exists and val_delta <= 0:
            score -= 0.8
            reasons.append("patch_without_validation_gain")
    elif contract == TaskContract.VALIDATE:
        if val_delta > 0:
            score += 0.8
        elif val_delta < 0:
            score -= 0.8

    if score >= 1.5:
        classification = DeltaClassification.STRONG_IMPROVEMENT
    elif score >= 0.4:
        classification = DeltaClassification.WEAK_IMPROVEMENT
    elif score <= -1.0:
        classification = DeltaClassification.REGRESSION
    elif -0.2 <= score <= 0.2:
        classification = DeltaClassification.NO_IMPROVEMENT
    else:
        classification = DeltaClassification.AMBIGUOUS

    return VerificationDelta(
        classification=classification,
        score=round(score, 2),
        reason=", ".join(reasons) if reasons else "insufficient_delta_evidence",
        details={
            "validation_delta": val_delta,
            "new_failure_fingerprints": new_fps,
            "sharper_localization": sharper_localization,
            "validation_delta_status": str(validation_delta.get("status", "unchanged")),
            "tool_error_delta": tool_error_delta,
        },
    )


def classify_node_outcome(
    contract_type: str,
    static_result: dict[str, Any],
    command_results: list[dict[str, Any]],
    changed_files: list[str],
    prose_only: bool = False,
    localization: dict[str, Any] | None = None,
    prior_fingerprints: list[str] | None = None,
    previous_localization: dict[str, Any] | None = None,
    baseline: VerificationBaseline | None = None,
    validation_summary: dict[str, Any] | None = None,
    execution_payload: dict[str, Any] | None = None,
    validation_delta: dict[str, Any] | None = None,
    mission_type: str = "",
    node_phase: str = "",
    clarification_requested: bool = False,
) -> dict[str, Any]:
    contract = normalize_task_contract(contract_type)
    localization = localization or {}
    prior_fingerprints = prior_fingerprints or []
    previous_localization = previous_localization or {}
    validation_summary = validation_summary or {}
    execution_payload = execution_payload or {}
    validation_delta = validation_delta or {"status": "unchanged"}
    baseline = baseline or VerificationBaseline()

    command_fail = any(int(r.get("exit", 0)) != 0 for r in command_results)
    any_command = bool(command_results)
    repeated_in_run = any(bool(r.get("repeated_failure")) for r in command_results)
    failure_fingerprints = [str(r.get("failure_fingerprint", "")) for r in command_results if r.get("failure_fingerprint")]
    repeated_across_history = any(fp in prior_fingerprints for fp in failure_fingerprints)
    repeated_failure = repeated_in_run or repeated_across_history
    user_space_changes = [p for p in changed_files if not _is_internal_artifact(str(p))]
    internal_only_patch = bool(changed_files) and not bool(user_space_changes)
    docs_only_user_space = bool(user_space_changes) and all(_is_docs_only_path(p) for p in user_space_changes)
    patch_exists = bool(changed_files)
    user_deliverable_patch = bool(user_space_changes)
    meaningful_patch = bool(static_result.get("meaningful_patch"))
    suspicious_breadth = bool(static_result.get("suspicious_breadth"))
    has_localization = _has_useful_localization(localization)
    prev_conf = float(previous_localization.get("confidence", 0.0) or 0.0)
    loc_conf = float(localization.get("confidence", 0.0) or 0.0)
    localization_stale = bool(localization) and loc_conf <= prev_conf and list(localization.get("target_files", [])) == list(previous_localization.get("target_files", []))

    delta = _classify_delta(
        contract,
        patch_exists=patch_exists,
        meaningful_patch=meaningful_patch,
        localization=localization,
        previous_localization=previous_localization,
        baseline=baseline,
        validation_summary=validation_summary,
        failure_fingerprints=failure_fingerprints,
        suspicious_breadth=suspicious_breadth,
        execution_payload=execution_payload,
        validation_delta=validation_delta,
    )

    status = "partial"
    reason = "insufficient evidence"

    if clarification_requested:
        status, reason = "failed", "autonomous node asked user for confirmation/clarification"
    elif prose_only:
        status, reason = "stale", "runner produced prose-only output"
    elif contract == TaskContract.LOCALIZE:
        if delta.classification in {DeltaClassification.STRONG_IMPROVEMENT, DeltaClassification.WEAK_IMPROVEMENT}:
            status, reason = "passed", "localization sharpened or gained confidence"
        elif localization:
            status, reason = "partial", "localization evidence weak or unchanged"
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
        elif delta.classification == DeltaClassification.REGRESSION:
            status, reason = "failed", "patch regressed validation state"
        elif delta.classification == DeltaClassification.NO_IMPROVEMENT:
            status, reason = "failed", "patch produced no measurable improvement"
        elif any_command and not command_fail:
            status, reason = "passed", "patch validated successfully"
        elif meaningful_patch and delta.classification == DeltaClassification.WEAK_IMPROVEMENT:
            status, reason = "partial", "patch exists with weak signal of improvement"
        else:
            status, reason = "failed", "patch failed validation"
    elif contract == TaskContract.VALIDATE:
        if not any_command:
            status, reason = "failed", "validate node ran no commands"
        elif delta.classification == DeltaClassification.REGRESSION:
            status, reason = "failed", "validation outcomes regressed"
        elif validation_delta.get("status") == "partially_improved":
            status, reason = "partial", "validation partially improved"
        elif delta.classification in {DeltaClassification.STRONG_IMPROVEMENT, DeltaClassification.WEAK_IMPROVEMENT}:
            status, reason = "passed", "validation state improved"
        elif command_fail:
            status, reason = "failed", "validation commands failed"
        else:
            status, reason = "partial", "validation ran without measurable delta"
    elif contract == TaskContract.SUMMARIZE:
        if patch_exists:
            status, reason = "failed", "summarize node edited files"
        elif prose_only:
            status, reason = "partial", "summary too thin"
        else:
            status, reason = "passed", "summary completed without edits"
    elif not contract_allows_edits(contract):
        status, reason = ("passed", "non-edit contract satisfied") if (any_command or static_result.get("findings") == []) else ("partial", "non-edit node incomplete")

    if mission_type == "greenfield_build":
        if clarification_requested:
            status, reason = "failed", "greenfield autonomous execution asked for confirmation"
        elif internal_only_patch:
            status, reason = "failed", "greenfield changes were internal artifacts only (.villani/.villani_code)"
        elif node_phase in {"scaffold_project", "implement_vertical_slice"}:
            if not user_deliverable_patch:
                status, reason = "failed", "no user-space deliverable created outside internal artifact folders"
            elif docs_only_user_space:
                status, reason = "failed", "greenfield build produced docs-only output; runnable artifacts required"
            else:
                status, reason = "passed", "user-space deliverable created for greenfield build"
        elif node_phase == "validate_project":
            if not any_command:
                status, reason = "failed", "greenfield validation ran no commands"
            elif command_fail:
                status, reason = "failed", "greenfield validation commands failed"
            else:
                status, reason = "passed", "greenfield validation evidence captured"
        elif node_phase == "summarize_outcome":
            if patch_exists:
                status, reason = "failed", "greenfield summary node edited files"
            elif prose_only:
                status, reason = "partial", "summary too thin"
            else:
                status, reason = "passed", "greenfield outcome summarized"

    validation_worsened = delta.classification == DeltaClassification.REGRESSION and patch_exists
    patch_no_improvement = patch_exists and delta.classification in {DeltaClassification.NO_IMPROVEMENT, DeltaClassification.AMBIGUOUS, DeltaClassification.REGRESSION}

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
        "clarification_requested": clarification_requested,
        "changed_files": list(changed_files),
        "user_space_changed_files": user_space_changes,
        "user_deliverable_patch": user_deliverable_patch,
        "internal_only_patch": internal_only_patch,
        "docs_only_user_space": docs_only_user_space,
        "failure_fingerprints": failure_fingerprints,
        "localization_weak": bool(localization) and not has_localization,
        "localization_stale": localization_stale,
        "delta_classification": delta.classification.value,
        "delta_score": delta.score,
        "delta_reason": delta.reason,
        "delta_details": delta.details,
        "validation_delta": validation_delta,
    }
