from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from villani_code.autonomy import TaskContract, contract_allows_edits, normalize_task_contract
from villani_code.mission import DeltaClassification, MissionScratchpad
from villani_code.path_authority import is_internal_villani_path


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


def _has_structured_objective(objective: dict[str, Any]) -> bool:
    if not isinstance(objective, dict):
        return False
    required = (
        "repo_state_type",
        "task_shape",
        "deliverable_kind",
        "direction",
        "initial_validation_strategy",
    )
    for key in required:
        value = objective.get(key)
        if key in {"deliverable_kind", "initial_validation_strategy"}:
            if not list(value or []):
                return False
        elif not str(value or "").strip():
            return False
    return True


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
    scratchpad: MissionScratchpad | None = None,
    mission_objective: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = normalize_task_contract(contract_type)
    localization = localization or {}
    prior_fingerprints = prior_fingerprints or []
    previous_localization = previous_localization or {}
    validation_summary = validation_summary or {}
    execution_payload = execution_payload or {}
    validation_delta = validation_delta or {"status": "unchanged"}
    baseline = baseline or VerificationBaseline()
    self_reported_validation_without_evidence = bool(execution_payload.get("self_reported_validation_without_evidence", False))
    attempted_write_paths = [str(p) for p in list(execution_payload.get("attempted_write_paths", []) or []) if str(p).strip()]
    blocked_write_paths = [str(p) for p in list(execution_payload.get("blocked_write_paths", []) or []) if str(p).strip()]
    rejected_actions = list(execution_payload.get("rejected_actions", []) or [])
    shell_invocations = [str(c) for c in list(execution_payload.get("shell_invocations", []) or []) if str(c).strip()]
    approved_actions = list(execution_payload.get("approved_actions", []) or [])
    mission_objective = mission_objective or {}
    controller_findings = [str(x) for x in list(execution_payload.get("controller_findings", []) or []) if str(x).strip()]
    controller_objective = dict(execution_payload.get("controller_objective", {}) or {})
    has_controller_objective = _has_structured_objective(controller_objective) or _has_structured_objective(mission_objective)
    has_controller_read_only_evidence = bool(controller_findings or approved_actions)
    prose_only_override_allowed = bool(
        mission_type == "greenfield_build"
        and node_phase in {"inspect_workspace", "define_objective"}
        and (has_controller_read_only_evidence or has_controller_objective)
    )
    phase_for_validation = str(node_phase or "").strip().lower()
    validation_relevant_results = command_results
    if phase_for_validation in {"inspect_workspace", "define_objective", "summarize_outcome"}:
        validation_relevant_results = []

    command_fail = any(int(r.get("exit", 0)) != 0 for r in validation_relevant_results)
    any_command = bool(validation_relevant_results)
    any_probe_command = bool(command_results)
    validation_status = "validation_unproven"
    if any_command and command_fail:
        validation_status = "validated_fail"
    elif any_command and not command_fail:
        validation_status = "validated_pass"
    build_like_command = any(
        any(token in str(record.get("command", "")).lower() for token in ("npm run build", "python -m build", "cargo build", "mvn ", "gradle build", "make "))
        for record in command_results
    )
    repeated_in_run = any(bool(r.get("repeated_failure")) for r in command_results)
    failure_fingerprints = [str(r.get("failure_fingerprint", "")) for r in command_results if r.get("failure_fingerprint")]
    repeated_across_history = any(fp in prior_fingerprints for fp in failure_fingerprints)
    repeated_failure = repeated_in_run or repeated_across_history
    user_space_changes = [p for p in changed_files if not is_internal_villani_path(str(p))]
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

    contract_violation = False
    status = "partial"
    reason = "insufficient evidence"

    if clarification_requested:
        status, reason = "failed", "autonomous node asked user for confirmation/clarification"
    elif prose_only and not prose_only_override_allowed:
        status, reason = "stale", "runner produced prose-only output"
    elif contract == TaskContract.LOCALIZE:
        if delta.classification in {DeltaClassification.STRONG_IMPROVEMENT, DeltaClassification.WEAK_IMPROVEMENT}:
            status, reason = "passed", "localization sharpened or gained confidence"
        elif localization:
            status, reason = "partial", "localization evidence weak or unchanged"
        else:
            status, reason = "failed", "no localization evidence produced"
    elif contract == TaskContract.INSPECT:
        if has_localization or bool(static_result.get("findings")) or any_probe_command:
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
        elif meaningful_patch and delta.classification in {DeltaClassification.WEAK_IMPROVEMENT, DeltaClassification.AMBIGUOUS}:
            status, reason = "partial", "patch exists with useful progress but lacks decisive validation"
        else:
            status, reason = "failed", "patch failed validation"
    elif contract == TaskContract.VALIDATE:
        if not any_command and self_reported_validation_without_evidence:
            status, reason = "failed", "validate node claimed success in prose without command evidence"
        elif not any_command:
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
            contract_violation = True
        elif attempted_write_paths or shell_invocations:
            status, reason = "failed", "summarize node attempted effectful operations"
            contract_violation = True
        elif prose_only:
            status, reason = "partial", "summary too thin"
        else:
            status, reason = "passed", "summary completed without edits"
    elif not contract_allows_edits(contract):
        status, reason = ("passed", "non-edit contract satisfied") if (any_command or static_result.get("findings") == []) else ("partial", "non-edit node incomplete")

    if mission_type == "greenfield_build":
        if scratchpad and scratchpad.mission_type == "greenfield_build" and mission_type != "greenfield_build":
            mission_type = "greenfield_build"
        if clarification_requested:
            status, reason = "failed", "greenfield autonomous execution asked for confirmation"
        elif internal_only_patch:
            status, reason = "failed", "greenfield changes were internal artifacts only (.villani/.villani_code)"
        elif node_phase in {"scaffold_project", "implement_increment"}:
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
                contract_violation = True
            elif attempted_write_paths:
                status, reason = "failed", "greenfield summary node attempted write operations"
                contract_violation = True
            elif shell_invocations:
                status, reason = "failed", "greenfield summary node attempted effectful shell actions"
                contract_violation = True
            elif prose_only:
                status, reason = "partial", "summary too thin"
            else:
                status, reason = "passed", "greenfield outcome summarized"
        elif node_phase == "inspect_workspace":
            inspection_signals = bool(
                has_localization or static_result.get("findings") or any_probe_command or approved_actions or controller_findings
            )
            if patch_exists:
                status, reason = "failed", "contract violation: inspect_workspace is read-only but wrote files"
                contract_violation = True
            elif attempted_write_paths:
                status, reason = "partial", "inspect gathered signals but attempted forbidden writes in read-only phase"
                contract_violation = True
            elif inspection_signals:
                status, reason = "passed", "inspect captured workspace evidence and constraints for planning"
            else:
                status, reason = "partial", "inspect remained read-only but did not capture enough workspace evidence"
        elif node_phase == "define_objective":
            objective_complete = _has_structured_objective(mission_objective) or _has_structured_objective(controller_objective)
            effective_objective = mission_objective if _has_structured_objective(mission_objective) else controller_objective
            objective_partial = bool(effective_objective.get("direction") or effective_objective.get("deliverable_kind"))
            if patch_exists:
                status, reason = "failed", "contract violation: define_objective is read-only but wrote files"
                contract_violation = True
            elif attempted_write_paths:
                status, reason = "partial", "objective synthesis attempted forbidden writes in read-only phase"
                contract_violation = True
            elif objective_complete:
                status, reason = "passed", "structured authoritative objective synthesized and stored"
            elif objective_partial:
                status, reason = "partial", "objective exists but is missing required structured fields"
            else:
                status, reason = "partial", "objective synthesis did not produce required structured fields"
        read_only_phases = {"inspect_workspace", "define_objective", "summarize_outcome"}
        if node_phase in read_only_phases and patch_exists:
            status, reason = "failed", f"contract violation: {node_phase} is read-only but wrote files"
            contract_violation = True
        if node_phase in {"inspect_workspace", "define_objective"} and build_like_command:
            status, reason = "failed", f"contract violation: {node_phase} should not run full build commands"
            contract_violation = True
        if node_phase == "validate_project" and self_reported_validation_without_evidence:
            status, reason = "failed", "greenfield validation claimed success without command evidence"
    if scratchpad:
        confirmed = list(scratchpad.confirmed_deliverables or [])
        if confirmed and not user_space_changes and status == "failed":
            status, reason = "partial", "scratchpad confirms prior deliverables; avoid false no-deliverable regression"
        chosen = str(scratchpad.chosen_project_direction).strip()
        if mission_type == "greenfield_build" and chosen and scratchpad.no_confirmation_required and clarification_requested:
            status, reason = "failed", f"direction '{chosen}' is authoritative; confirmation prompts are invalid"

    validation_worsened = delta.classification == DeltaClassification.REGRESSION and patch_exists
    patch_no_improvement = patch_exists and delta.classification in {DeltaClassification.NO_IMPROVEMENT, DeltaClassification.AMBIGUOUS, DeltaClassification.REGRESSION}
    phase_contract_status = "contract_clean_success"
    mission_progress_status = "no_progress"
    read_only_state_advanced = False
    if node_phase == "inspect_workspace":
        read_only_state_advanced = bool(
            status in {"passed", "partial"}
            and (has_localization or static_result.get("findings") or approved_actions or any_command or controller_findings)
        )
    elif node_phase == "define_objective":
        effective_objective = mission_objective if _has_structured_objective(mission_objective) else controller_objective
        read_only_state_advanced = bool(
            status in {"passed", "partial"}
            and (
                _has_structured_objective(effective_objective)
                or effective_objective.get("direction")
                or effective_objective.get("initial_validation_strategy")
                or (scratchpad and (scratchpad.chosen_project_direction or scratchpad.next_required_action))
            )
        )

    if node_phase in {"inspect_workspace", "define_objective"}:
        if status == "passed" and read_only_state_advanced:
            mission_progress_status = "state_progress"
        elif status == "partial" and read_only_state_advanced:
            mission_progress_status = "state_progress_partial"
        elif status == "failed" and (patch_exists or attempted_write_paths):
            mission_progress_status = "useful_progress_with_contract_violation"
    elif node_phase in {"scaffold_project", "implement_increment"}:
        if status in {"passed", "partial"} and patch_exists:
            mission_progress_status = "artifact_progress"
        elif status == "failed" and patch_exists:
            mission_progress_status = "useful_progress_with_contract_violation"
    elif node_phase == "validate_project":
        if status == "passed" and any_command and not command_fail:
            mission_progress_status = "validated_success"
        elif status == "partial" and any_command:
            mission_progress_status = "validation_progress"
        elif status == "failed" and any_command:
            mission_progress_status = "validated_fail"
    elif node_phase == "summarize_outcome":
        if status == "passed":
            mission_progress_status = "summary_completed"
        elif status == "partial":
            mission_progress_status = "summary_partial"
        elif status == "failed" and (patch_exists or attempted_write_paths):
            mission_progress_status = "useful_progress_with_contract_violation"
    elif status == "passed" and patch_exists and not any_command:
        mission_progress_status = "useful_progress_unvalidated"
    elif status == "passed" and any_command and not command_fail:
        mission_progress_status = "validated_success"
    elif status == "failed" and patch_exists:
        mission_progress_status = "useful_progress_with_contract_violation"
    elif status == "partial" and patch_exists:
        mission_progress_status = "useful_progress_unvalidated"
    elif status == "failed" and any_command:
        mission_progress_status = "validated_fail"
    if status in {"failed", "stale"}:
        phase_contract_status = "contract_violation"
    elif status == "partial":
        phase_contract_status = "contract_partial"
    if mission_progress_status == "no_progress" and not patch_exists and not any_command:
        mission_progress_status = "no_progress"
    elif mission_progress_status == "no_progress" and any_command and command_fail and node_phase == "validate_project":
        mission_progress_status = "validated_fail"
    if blocked_write_paths:
        mission_progress_status = "blocked"
    if rejected_actions and not changed_files:
        phase_contract_status = "contract_violation_recovered"
    if contract_violation and mission_progress_status == "useful_progress_unvalidated":
        mission_progress_status = "useful_progress_with_contract_violation"
    if status == "passed" and delta.classification == DeltaClassification.REGRESSION:
        delta = VerificationDelta(
            classification=DeltaClassification.AMBIGUOUS,
            score=delta.score,
            reason=f"{delta.reason}, normalized_from_regression_on_pass",
            details=delta.details,
        )
        validation_worsened = False

    return {
        "status": status,
        "reason": reason,
        "phase_contract_status": phase_contract_status,
        "mission_progress_status": mission_progress_status,
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
        "self_reported_validation_without_evidence": self_reported_validation_without_evidence,
        "self_reported_validation_claim": bool(execution_payload.get("self_reported_validation_claim", False)),
        "verification_status": (
            "validation_self_reported_unverified"
            if self_reported_validation_without_evidence
            else validation_status
        ),
        "attempted_write_paths": attempted_write_paths,
        "blocked_write_paths": blocked_write_paths,
        "shell_invocations": shell_invocations,
        "contract_violation": contract_violation or bool(rejected_actions),
        "rejected_actions": rejected_actions,
    }
