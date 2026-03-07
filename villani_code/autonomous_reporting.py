from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.evidence import parse_command_evidence
from villani_code.repo_rules import is_ignored_repo_path


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
