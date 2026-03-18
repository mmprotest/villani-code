from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from villani_code.localization import (
    BenchmarkLocalizationPack,
    VerificationFailureEvidence,
    classify_verification_failure,
)
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.tools import tool_specs
from villani_code.validation_loop import ValidationResult, run_validation


@dataclass(slots=True)
class RepairContext:
    task_summary: str
    plan_summary: str
    change_impact: str
    files_changed: list[str]
    failing_validation_step: str
    failure_summary: str
    benchmark_contract: dict[str, Any] = field(default_factory=dict)
    blocked_paths: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    support_files: list[str] = field(default_factory=list)
    localization_pack: dict[str, Any] = field(default_factory=dict)
    already_touched_files: list[str] = field(default_factory=list)
    previous_edit_summary: str = ""
    failing_verifier_output: str = ""
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    edit_authority: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RepairAttemptSummary:
    attempt: int
    failing_step: str
    failure_summary: str
    repair_summary: str
    status: str
    branch_name: str = ""
    branch_reason: str = ""
    targeted_verification_passed: bool | None = None
    winner_reason: str = ""


@dataclass(slots=True)
class RepairOutcome:
    recovered: bool
    message: str
    attempts: list[RepairAttemptSummary] = field(default_factory=list)
    last_failed_step: str = ""
    failure_classification: str = ""
    environment_harness_failure: bool = False
    branching_occurred: bool = False
    branch_count: int = 0


@dataclass(slots=True)
class RepairBranch:
    name: str
    reason: str
    target_files: list[str]


def _pack_to_payload(pack: BenchmarkLocalizationPack | None) -> dict[str, Any]:
    if pack is None:
        return {}
    return {
        "repo_map_summary": pack.repo_map_summary,
        "likely_test_roots": pack.likely_test_roots,
        "likely_source_roots": pack.likely_source_roots,
        "expected_task_files": pack.expected_task_files,
        "top_candidate_files": [
            {
                "path": candidate.path,
                "authority_tier": candidate.authority_tier,
                "reasons": candidate.reasons,
                "symbols": candidate.symbols,
            }
            for candidate in pack.top_candidate_files
        ],
        "related_symbols": pack.related_symbols,
        "related_imports": pack.related_imports,
    }


def _previous_edit_summary(changed_files: list[str]) -> str:
    if not changed_files:
        return "No bounded patch landed yet."
    return f"Previous bounded patch touched: {', '.join(changed_files[:8])}."


def _build_repair_context(
    runner: Any,
    changed_files: list[str],
    failing_step: str,
    failure_summary: str,
    failure: VerificationFailureEvidence,
) -> RepairContext:
    benchmark = getattr(runner, "benchmark_config", None)
    localization_pack = getattr(runner, "_benchmark_localization_pack", None)
    authority = {
        candidate.path: {
            "tier": candidate.authority_tier,
            "reasons": candidate.reasons,
        }
        for candidate in getattr(localization_pack, "top_candidate_files", [])
    }
    verification_history = list(getattr(runner, "_verification_history", []))[-6:]
    task_contract = dict(getattr(runner, "_task_contract", {}))
    return RepairContext(
        task_summary=str(getattr(getattr(runner, "_execution_plan", None), "task_goal", ""))[:200],
        plan_summary=getattr(getattr(runner, "_execution_plan", None), "to_human_text", lambda: "")()[:500],
        change_impact="source_only",
        files_changed=changed_files[:10],
        failing_validation_step=failing_step,
        failure_summary=str(failure_summary)[:500],
        benchmark_contract=task_contract,
        blocked_paths=list(getattr(benchmark, "forbidden_paths", []) if benchmark else []),
        expected_files=list(getattr(benchmark, "expected_files", []) if benchmark else []),
        support_files=list(getattr(benchmark, "allowed_support_files", []) if benchmark else []),
        localization_pack=_pack_to_payload(localization_pack),
        already_touched_files=sorted(set(changed_files) | set(getattr(runner, "_intended_targets", set())))[:10],
        previous_edit_summary=_previous_edit_summary(changed_files),
        failing_verifier_output=failure.raw_excerpt[:1200],
        verification_history=verification_history,
        edit_authority=authority,
    )


def _build_branches(
    failure: VerificationFailureEvidence,
    context: RepairContext,
    localization_pack: BenchmarkLocalizationPack | None,
) -> list[RepairBranch]:
    branches: list[RepairBranch] = []
    candidate_paths = [
        candidate.path for candidate in getattr(localization_pack, "top_candidate_files", []) if candidate.authority_tier <= 4
    ]
    primary = context.files_changed[:1] or failure.targeted_candidates[:1] or context.expected_files[:1] or candidate_paths[:1]
    if primary:
        branches.append(RepairBranch("same-file-correction", "repair the previous file with a narrower logic change", primary[:1]))
    adjacent = [path for path in candidate_paths if path not in primary][:2]
    if failure.repair_decision in {"missing_adjacent_change", "wrong_file_localized"} and adjacent:
        branches.append(RepairBranch("adjacent-file-correction", "keep scope bounded but include the most suspicious adjacent implementation/test file", adjacent[:1]))
    elif adjacent:
        branches.append(RepairBranch("localization-shift", "shift to the strongest alternate localization candidate from verifier evidence", adjacent[:1]))
    return branches[:2]


def _run_repair_prompt(
    runner: Any,
    context: RepairContext,
    prior_attempts: list[RepairAttemptSummary],
    branch: RepairBranch,
) -> str:
    payload = {
        "repair_mode": "bounded_benchmark_repair",
        "branch": asdict(branch),
        "context": asdict(context),
        "prior_attempts": [asdict(a) for a in prior_attempts],
        "instruction": (
            "Repair only the failing validation signal with one minimal corrective patch. "
            "Stay within allowed files, inherit benchmark scope, and do not reset or explore broadly."
        ),
    }
    prompt = "Bounded repair workflow JSON:\n" + json.dumps(payload, ensure_ascii=False)
    call_messages = build_initial_messages(runner.repo, prompt)
    raw = runner.client.create_message(
        {
            "model": runner.model,
            "messages": call_messages,
            "system": build_system_blocks(
                runner.repo,
                repo_map=runner._repo_map if (runner.small_model or runner.benchmark_config.enabled) else "",
                benchmark_config=runner.benchmark_config,
                task_mode=getattr(runner, "_task_mode", None),
                benchmark_localization_pack=getattr(runner, "_benchmark_localization_pack", None),
            ),
            "tools": tool_specs(),
            "max_tokens": runner.max_tokens,
            "stream": False,
        },
        stream=False,
    )
    response = raw if isinstance(raw, dict) else {"content": []}
    for block in [b for b in response.get("content", []) if b.get("type") == "tool_use"]:
        runner._execute_tool_with_policy(
            str(block.get("name", "")),
            dict(block.get("input", {})),
            str(block.get("id", "repair-tool")),
            len(call_messages),
        )
    text = "\n".join(
        b.get("text", "")
        for b in response.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    )
    return text[:400] or f"{branch.name} executed"


def execute_repair_loop(
    runner: Any,
    repo: Path,
    changed_files: list[str],
    initial_validation: ValidationResult,
    repo_map: dict[str, Any],
    change_impact: str | None,
    action_classes: list[str] | None,
    max_attempts: int,
) -> RepairOutcome:
    attempts: list[RepairAttemptSummary] = []
    failing_step = initial_validation.steps[-1].step.name if initial_validation.steps else (initial_validation.structured_failure.step_name if initial_validation.structured_failure else "unknown")
    failure_summary = initial_validation.structured_failure.concise_summary if initial_validation.structured_failure else initial_validation.failure_summary
    failure = classify_verification_failure(
        failure_summary,
        compact_output=initial_validation.structured_failure.compact_output if initial_validation.structured_failure else initial_validation.failure_summary,
        relevant_paths=initial_validation.structured_failure.relevant_paths if initial_validation.structured_failure else [],
    )
    structured_class = getattr(initial_validation.structured_failure, "failure_class", "") if initial_validation.structured_failure else ""
    if structured_class in {
        "src_layout_import_error",
        "missing_make",
        "shell_incompatibility",
        "missing_command_runner_dependency",
    }:
        failure.classification = structured_class
        failure.repair_decision = "environment_harness_issue"
        failure.environment_failure = True
    localization_pack = getattr(runner, "_benchmark_localization_pack", None)
    context = _build_repair_context(runner, changed_files, failing_step, failure_summary, failure)
    branches = _build_branches(failure, context, localization_pack)
    branching_occurred = len(branches) > 1

    runner.event_callback(
        {
            "type": "repair_mode_entered",
            "failing_step": failing_step,
            "repair_classification": failure.classification,
            "repair_decision": failure.repair_decision,
            "environment_harness_failure": failure.environment_failure,
            "allowed_files": sorted(set(context.expected_files + context.support_files + context.already_touched_files))[:10],
            "already_changed_files": context.already_touched_files,
        }
    )
    if failure.environment_failure:
        runner.event_callback(
            {
                "type": "repair_environment_harness_failure",
                "classification": failure.classification,
                "summary": failure.summary,
            }
        )
        return RepairOutcome(
            False,
            f"Repair stopped: verifier failure classified as environment/harness issue ({failure.classification}).",
            attempts,
            failing_step,
            failure_classification=failure.classification,
            environment_harness_failure=True,
            branching_occurred=False,
            branch_count=0,
        )

    runner.event_callback(
        {
            "type": "repair_branching_started",
            "branching_occurred": branching_occurred,
            "branch_count": len(branches),
            "branches": [asdict(branch) for branch in branches],
        }
    )

    best_failure = failure_summary
    best_step = failing_step
    for attempt_idx, branch in enumerate(branches[: max(1, min(max_attempts, 2))], start=1):
        runner.event_callback(
            {
                "type": "repair_attempt_started",
                "attempt": attempt_idx,
                "failing_step": failing_step,
                "branch_name": branch.name,
                "branch_reason": branch.reason,
                "target_files": branch.target_files,
            }
        )
        repair_summary = _run_repair_prompt(runner, context, attempts, branch)
        targeted = run_validation(
            repo,
            changed_files,
            event_callback=runner.event_callback,
            steps_override=[failing_step],
            repo_map=repo_map,
            change_impact=change_impact,
            action_classes=action_classes,
            task_mode=str(getattr(getattr(runner, "_task_mode", None), "value", getattr(runner, "_task_mode", "general"))),
        )
        runner.event_callback(
            {
                "type": "repair_targeted_verification_result",
                "attempt": attempt_idx,
                "branch_name": branch.name,
                "passed": targeted.passed,
            }
        )
        if targeted.passed:
            broader_result = None
            if initial_validation.plan.escalation.broaden_after_targeted_pass or initial_validation.plan.escalation.force_broad:
                runner.event_callback({"type": "validation_escalated", "reason": initial_validation.plan.escalation.reason})
                broader_result = run_validation(
                    repo,
                    changed_files,
                    event_callback=runner.event_callback,
                    repo_map=repo_map,
                    change_impact=change_impact,
                    action_classes=action_classes,
                    task_mode=str(getattr(getattr(runner, "_task_mode", None), "value", getattr(runner, "_task_mode", "general"))),
                )
            if broader_result is None or broader_result.passed:
                attempts.append(
                    RepairAttemptSummary(
                        attempt_idx,
                        failing_step,
                        str(failure_summary)[:220],
                        repair_summary[:260],
                        "recovered",
                        branch_name=branch.name,
                        branch_reason=branch.reason,
                        targeted_verification_passed=True,
                        winner_reason="verification outcome selected this branch",
                    )
                )
                runner.event_callback(
                    {
                        "type": "repair_attempt_result",
                        "attempt": attempt_idx,
                        "status": "recovered",
                        "branch_name": branch.name,
                        "winner_selection_reason": "verification outcome selected this branch",
                    }
                )
                return RepairOutcome(
                    True,
                    f"Validation recovered after bounded repair branch '{branch.name}'.",
                    attempts,
                    "",
                    failure_classification=failure.classification,
                    environment_harness_failure=False,
                    branching_occurred=branching_occurred,
                    branch_count=len(branches),
                )

        attempts.append(
            RepairAttemptSummary(
                attempt_idx,
                failing_step,
                str(failure_summary)[:220],
                repair_summary[:260],
                "failed",
                branch_name=branch.name,
                branch_reason=branch.reason,
                targeted_verification_passed=False,
            )
        )
        runner.event_callback(
            {
                "type": "repair_attempt_result",
                "attempt": attempt_idx,
                "status": "failed",
                "branch_name": branch.name,
            }
        )
        best_step = targeted.steps[-1].step.name if targeted.steps else best_step
        best_failure = targeted.structured_failure.concise_summary if targeted.structured_failure else targeted.failure_summary

    return RepairOutcome(
        False,
        "Validation failed after bounded repair branching. Remaining failure: " + str(best_failure)[:400],
        attempts,
        best_step,
        failure_classification=failure.classification,
        environment_harness_failure=False,
        branching_occurred=branching_occurred,
        branch_count=len(branches),
    )
