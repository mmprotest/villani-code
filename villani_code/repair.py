from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from villani_code.validation_loop import ValidationResult, run_validation
from villani_code.usage_events import emit_model_usage_event


def _normalize_repo_path(value: str) -> str:
    return str(value or "").replace("\\", "/").lstrip("./")


@dataclass(slots=True)
class RepairContext:
    task_summary: str
    plan_summary: str
    change_impact: str
    files_changed: list[str]
    failing_validation_step: str
    failure_summary: str


@dataclass(slots=True)
class RepairAttemptSummary:
    attempt: int
    failing_step: str
    failure_summary: str
    repair_summary: str
    status: str


@dataclass(slots=True)
class RepairOutcome:
    recovered: bool
    message: str
    attempts: list[RepairAttemptSummary] = field(default_factory=list)
    last_failed_step: str = ""


def _run_repair_prompt(runner: Any, context: RepairContext, prior_attempts: list[RepairAttemptSummary]) -> str:
    payload = {
        "task_summary": context.task_summary,
        "plan_summary": context.plan_summary,
        "change_impact": context.change_impact,
        "files_changed": context.files_changed,
        "failing_validation_step": context.failing_validation_step,
        "failure_summary": context.failure_summary,
        "prior_attempts": [asdict(a) for a in prior_attempts],
    }
    prompt = "Repair only the failing validation signal with minimal edits.\n" + json.dumps(payload, ensure_ascii=False)
    from villani_code.prompting import build_initial_messages, build_system_blocks
    from villani_code.tools import tool_specs

    call_messages = build_initial_messages(runner.repo, prompt)
    raw = runner.client.create_message({"model": runner.model, "messages": call_messages, "system": build_system_blocks(runner.repo), "tools": tool_specs(), "max_tokens": runner.max_tokens, "stream": False}, stream=False)
    response = raw if isinstance(raw, dict) else {"content": []}
    emit_model_usage_event(runner.event_callback, response, model=runner.model, phase="repair_loop")
    for block in [b for b in response.get("content", []) if b.get("type") == "tool_use"]:
        runner._execute_tool_with_policy(str(block.get("name", "")), dict(block.get("input", {})), str(block.get("id", "repair-tool")), len(call_messages))
    text = "\n".join(b.get("text", "") for b in response.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
    return text[:400] or "repair attempt executed"


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
    runner._repair_scope_lock_active = True
    runner._repair_locked_targets = {
        path for path in (_normalize_repo_path(item) for item in changed_files) if path
    }
    try:
        for attempt_idx in range(1, max_attempts + 1):
            runner.event_callback({"type": "repair_attempt_started", "attempt": attempt_idx, "failing_step": failing_step})
            context = RepairContext(
                task_summary=str(getattr(getattr(runner, "_execution_plan", None), "task_goal", ""))[:200],
                plan_summary=getattr(getattr(runner, "_execution_plan", None), "to_human_text", lambda: "")()[:500],
                change_impact=str(change_impact or "source_only"),
                files_changed=changed_files[:10],
                failing_validation_step=failing_step,
                failure_summary=str(failure_summary)[:500],
            )
            repair_summary = _run_repair_prompt(runner, context, attempts)

            targeted = run_validation(repo, changed_files, event_callback=runner.event_callback, steps_override=[failing_step], repo_map=repo_map, change_impact=change_impact, action_classes=action_classes)
            if targeted.passed:
                if initial_validation.plan.escalation.broaden_after_targeted_pass or initial_validation.plan.escalation.force_broad:
                    runner.event_callback({"type": "validation_escalated", "reason": initial_validation.plan.escalation.reason})
                    broader = run_validation(repo, changed_files, event_callback=runner.event_callback, repo_map=repo_map, change_impact=change_impact, action_classes=action_classes)
                    if broader.passed:
                        attempts.append(RepairAttemptSummary(attempt_idx, failing_step, str(failure_summary)[:220], repair_summary[:260], "recovered"))
                        runner.event_callback({"type": "repair_attempt_result", "attempt": attempt_idx, "status": "recovered"})
                        return RepairOutcome(True, f"Validation recovered after repair attempt {attempt_idx}.", attempts, "")
                else:
                    attempts.append(RepairAttemptSummary(attempt_idx, failing_step, str(failure_summary)[:220], repair_summary[:260], "recovered"))
                    runner.event_callback({"type": "repair_attempt_result", "attempt": attempt_idx, "status": "recovered"})
                    return RepairOutcome(True, f"Validation recovered after repair attempt {attempt_idx}.", attempts, "")

            attempts.append(RepairAttemptSummary(attempt_idx, failing_step, str(failure_summary)[:220], repair_summary[:260], "failed"))
            runner.event_callback({"type": "repair_attempt_result", "attempt": attempt_idx, "status": "failed"})
            failing_step = targeted.steps[-1].step.name if targeted.steps else failing_step
            failure_summary = targeted.structured_failure.concise_summary if targeted.structured_failure else targeted.failure_summary

        return RepairOutcome(False, "Validation failed after bounded repair attempts. Remaining failure: " + str(failure_summary)[:400], attempts, failing_step)
    finally:
        runner._repair_scope_lock_active = False
        runner._repair_locked_targets = set()
