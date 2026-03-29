from __future__ import annotations

from dataclasses import asdict

from pathlib import Path
from typing import Any

from villani_code.evidence import parse_command_evidence
from villani_code.repo_rules import is_ignored_repo_path
from villani_code.mission import Mission, MissionExecutionState
from villani_code.path_authority import split_internal_paths


def extract_runner_failures(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for event in result.get("transcript", {}).get("events", []):
        if event.get("type") != "failure_classified":
            continue
        category = str(event.get("category", "tool_failure"))
        summary = str(event.get("summary", ""))
        failures.append(f"{category}: {summary}".strip())
    for tool_result in result.get("transcript", {}).get("tool_results", []):
        if tool_result.get("is_error"):
            failures.append(f"tool_failure: {tool_result.get('content', '')}"[:280])
    return failures


def extract_commands(result: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tr in result.get("transcript", {}).get("tool_results", []):
        for record in parse_command_evidence(str(tr.get("content", ""))):
            out.append(
                {
                    "command": str(record.get("command", "")).strip(),
                    "exit": int(record.get("exit", 1)),
                }
            )
    return out


def detect_tooling_commands(files: list[str]) -> list[str]:
    commands: list[str] = []
    if any(f.startswith("tests/") for f in files):
        commands.append("pytest -q")
    return commands or ["git diff --stat"]


def todo_hits(repo: Path, files: list[str]) -> list[str]:
    hits: list[str] = []
    for rel in files:
        if len(hits) >= 20:
            break
        if is_ignored_repo_path(rel):
            continue
        if not rel.endswith((".py", ".md", ".txt")):
            continue
        path = repo / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "TODO" in line or "FIXME" in line:
                hits.append(f"{rel}: {line.strip()[:120]}")
                break
    return hits


def recommended_next_steps(attempted: list[Any], blocked_value: str, failed_values: set[str]) -> list[str]:
    if any(t.status == blocked_value for t in attempted):
        return [
            "Review blocked tasks and rerun with --unsafe only if trusted and necessary."
        ]
    if any(t.status in failed_values for t in attempted):
        return [
            "Inspect verification findings, then rerun Villani mode with tighter wave limits."
        ]
    return ["Run full CI before merging autonomous changes."]


def build_takeover_summary(
    *,
    state: Any,
    attempted: list[Any],
    current_changes: set[str],
    preexisting_changes: set[str],
    done_reason: str,
    recommended_next_steps_value: list[str],
    working_memory: dict[str, Any],
    blocked_value: str,
    opportunities_considered: int,
    opportunities_attempted: int,
) -> dict[str, Any]:
    preexisting = sorted(preexisting_changes)
    new_changes = sorted(current_changes - preexisting_changes)
    intentional_set = {p for t in attempted for p in t.intentional_changes}
    incidental_set = {p for t in attempted for p in t.incidental_changes}
    successful_tasks = sum(1 for t in attempted if t.status == "passed")
    failed_tasks = sum(1 for t in attempted if t.status in {"failed", "blocked", "retryable", "exhausted"})
    if not attempted:
        intentional_changes: list[str] = []
        incidental_changes: list[str] = []
    else:
        intentional_changes = sorted(intentional_set & set(new_changes))
        incidental_changes = sorted(incidental_set & set(new_changes))

    return {
        "repo_summary": state.repo_summary,
        "tasks_attempted": [
            {
                "id": t.task_id,
                "title": t.title,
                "status": t.status,
                "task_contract": t.task_contract,
                "attempts": t.attempts,
                "retries": t.retries,
                "reason": t.outcome[:1200],
                "verification": t.verification_results,
                "validation_artifacts": t.validation_artifacts,
                "inspection_summary": t.inspection_summary,
                "runner_failures": t.runner_failures,
                "produced_effect": t.produced_effect,
                "produced_validation": t.produced_validation,
                "produced_inspection_conclusion": t.produced_inspection_conclusion,
                "files_changed": t.files_changed,
                "intentional_changes": t.intentional_changes,
                "incidental_changes": t.incidental_changes,
                "terminated_reason": t.terminated_reason,
                "turns_used": t.turns_used,
                "tool_calls_used": t.tool_calls_used,
                "elapsed_seconds": t.elapsed_seconds,
                "completed": t.completed,
            }
            for t in attempted
        ],
        "files_changed": new_changes,
        "preexisting_changes": preexisting,
        "intentional_changes": intentional_changes,
        "incidental_changes": incidental_changes,
        "blockers": [t.title for t in attempted if t.status == blocked_value],
        "done_reason": done_reason,
        "opportunities_considered": opportunities_considered,
        "opportunities_attempted": opportunities_attempted,
        "successful_tasks": successful_tasks,
        "failed_tasks": failed_tasks,
        "completed_waves": state.completed_waves,
        "recommended_next_steps": recommended_next_steps_value,
        "working_memory": working_memory,
    }


def build_mission_summary(
    mission: Mission,
    execution_state: MissionExecutionState,
    *,
    files_touched: list[str],
    outcome: str,
    stop_reason: str,
) -> dict[str, Any]:
    nodes = []
    for node in mission.nodes:
        nodes.append(
            {
                "node_id": node.node_id,
                "title": node.title,
                "phase": node.phase.value,
                "status": node.status.value,
                "attempts": node.attempts,
                "contract_type": node.contract_type,
                "candidate_files": node.candidate_files,
                "evidence": node.evidence[-6:],
                "blockers": node.blockers,
                "last_outcome": asdict(node.last_outcome),
                "localization": asdict(node.localization),
            }
        )
    validations = execution_state.verification_history[-30:]
    validation_timeline = [
        {
            "node_id": item.get("node_id", ""),
            "failed": int((item.get("validation_summary", {}) or {}).get("failed", 0) or 0),
            "passed": int((item.get("validation_summary", {}) or {}).get("passed", 0) or 0),
            "delta": (item.get("validation_delta", {}) or {}).get("status", "unchanged"),
            "fingerprint": item.get("failure_fingerprint", ""),
            "evidence_kind": item.get("validation_evidence_kind", "none"),
            "self_reported_unverified": bool(item.get("self_reported_validation_without_evidence", False)),
        }
        for item in validations
    ]
    validation_evidence = {
        "real_validation_evidence_nodes": sorted(
            {
                str(item.get("node_id", ""))
                for item in validations
                if str(item.get("validation_evidence_kind", "")) == "real_command_results"
            }
        ),
        "self_reported_unverified_nodes": sorted(
            {
                str(item.get("node_id", ""))
                for item in validations
                if bool(item.get("self_reported_validation_without_evidence", False))
            }
        ),
    }
    localization_evolution = [
        {
            "targets": list(snapshot.target_files[:6]),
            "confidence": float(snapshot.confidence),
            "bug_class": snapshot.likely_bug_class,
            "intent": snapshot.repair_intent,
        }
        for snapshot in execution_state.localization_history[-12:]
    ]
    changed_by_status = {
        "succeeded": sorted({p for n in mission.nodes if n.status.value == "succeeded" for p in n.last_outcome.changed_files}),
        "failed": sorted({p for n in mission.nodes if n.status.value == "failed" for p in n.last_outcome.changed_files}),
    }
    blocked_write_attempts = sorted(
        {
            str(path)
            for item in validations
            for path in list((item.get("blocked_write_paths", []) or []))
            if str(path).strip()
        }
    )
    attempted_write_paths = sorted(
        {
            str(path)
            for item in validations
            for path in list((item.get("attempted_write_paths", []) or []))
            if str(path).strip()
        }
    )
    greenfield = {}
    scratchpad = execution_state.scratchpad
    if mission.mission_type.value == "greenfield_build":
        progress = dict(execution_state.greenfield_progress or {})
        persisted_deliverables = [str(p) for p in list(progress.get("deliverable_paths", []) or []) if str(p).strip()]
        merged_touched = sorted(dict.fromkeys(list(files_touched) + persisted_deliverables))
        files_touched, internal_artifacts = split_internal_paths(merged_touched)
        user_deliverables = list(files_touched)
        if not user_deliverables and persisted_deliverables:
            user_deliverables, _ = split_internal_paths(persisted_deliverables)
        greenfield = {
            "chosen_project_direction": scratchpad.chosen_project_direction or (mission.mission_context or {}).get("greenfield_selection", {}).get("project_type", ""),
            "selection_rationale": scratchpad.selection_rationale or (mission.mission_context or {}).get("greenfield_selection", {}).get("selection_rationale", ""),
            "project_candidates": list((mission.mission_context or {}).get("greenfield_candidates", [])),
            "user_space_deliverables": user_deliverables,
            "internal_artifacts": internal_artifacts,
            "runnable_slice": bool(user_deliverables),
            "successful_greenfield_scaffold": bool(progress.get("successful_greenfield_scaffold")),
            "validation_state": "proven" if bool(scratchpad.validation_proven) else "unproven",
            "mission_completion_state": "complete" if bool(scratchpad.validation_proven and user_deliverables and scratchpad.has_runnable_entrypoint) else "partial",
            "remaining_next_action": "" if bool(scratchpad.validation_proven) else (scratchpad.next_required_action or "validate_project"),
            "no_regression_guard": bool(user_deliverables),
            "validation_truth": {
                "authoritative_command_evidence": bool(validation_evidence["real_validation_evidence_nodes"]),
                "self_reported_unverified": bool(validation_evidence["self_reported_unverified_nodes"]),
            },
            "write_accounting": {
                "actual_user_space_changes": sorted(set(files_touched)),
                "attempted_write_paths": attempted_write_paths,
                "blocked_write_paths": blocked_write_attempts,
            },
            "run_instructions": "Run the generated project entrypoint and listed validation commands from mission evidence.",
        }
    return {
        "mission_id": mission.mission_id,
        "mission_goal": mission.user_goal,
        "mission_type": mission.mission_type.value,
        "mission_scratchpad": {
            "mission_goal": scratchpad.mission_goal,
            "mission_type": scratchpad.mission_type,
            "chosen_project_direction": scratchpad.chosen_project_direction,
            "selection_rationale": scratchpad.selection_rationale,
            "ignored_internal_paths": list(scratchpad.ignored_internal_paths),
            "confirmed_deliverables": list(scratchpad.confirmed_deliverables),
            "current_phase": scratchpad.current_phase,
            "last_successful_action": scratchpad.last_successful_action,
            "next_required_action": scratchpad.next_required_action,
            "current_blockers": list(scratchpad.current_blockers),
        },
        "nodes_executed": nodes,
        "files_inspected": sorted(set(execution_state.inspected_files)),
        "files_touched": sorted(set(files_touched)),
        "changed_count": len(sorted(set(files_touched))),
        "evidence": execution_state.evidence_log[-40:],
        "validation_results": validations,
        "validation_timeline": validation_timeline,
        "validation_evidence": validation_evidence,
        "validation_truth_statement": (
            "Validation backed by real command evidence."
            if validation_evidence["real_validation_evidence_nodes"]
            else "Validation not proven by command evidence; any prose claims are self-reported/unverified."
        ),
        "verification_status_timeline": [
            {
                "node_id": item.get("node_id", ""),
                "verification_status": item.get("verification_status", "validation_unproven"),
            }
            for item in validations
        ],
        "localization_evolution": localization_evolution,
        "failure_fingerprint_evolution": [fp for fp in execution_state.failure_fingerprint_history[-20:] if fp],
        "changed_files_by_attempt_outcome": changed_by_status,
        "greenfield_report": greenfield,
        "final_outcome": outcome,
        "stop_reason": stop_reason,
        "blocked_reason": stop_reason if outcome == "blocked" else "",
    }
