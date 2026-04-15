from __future__ import annotations

import json
from dataclasses import asdict

from villani_code.orchestrator_models import Subtask, SupervisorResult


def build_supervisor_instruction(objective: str, max_workers: int) -> str:
    example = SupervisorResult(
        subtasks=[
            Subtask(
                id="task_1",
                goal="Bounded implementation step",
                success_criteria=["Objective progresses materially"],
                target_files=["path/to/file.py"],
                scope_hint="Keep the patch minimal",
            )
        ]
    )
    return (
        "You are the supervisor role for Villani Code orchestrator. "
        "Return strict JSON only with this shape: "
        f"{json.dumps(asdict(example), separators=(',', ':'))}. "
        f"Produce between 1 and {max_workers} subtasks. "
        "No markdown. No prose. No code edits. "
        f"Top-level objective:\n{objective}"
    )


def build_worker_instruction(
    objective: str,
    subtask_payload: dict[str, object],
    previous_failure: str | None = None,
) -> str:
    retry_suffix = ""
    if previous_failure:
        retry_suffix = f"\nRetry context from previous attempt: {previous_failure}\n"
    return (
        "You are the worker role for Villani Code orchestrator. "
        "Edit files directly in this repository to complete the bounded subtask. "
        "Return strict JSON only with keys: status, summary, files_touched, recommended_verification. "
        "Allowed status values: success, blocked_environment, blocked_scope, failed. "
        "No markdown. No prose outside JSON. "
        f"Top-level objective:\n{objective}\n"
        f"Assigned subtask JSON:\n{json.dumps(subtask_payload, ensure_ascii=False)}"
        f"{retry_suffix}"
    )
