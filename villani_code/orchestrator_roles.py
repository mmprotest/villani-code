from __future__ import annotations

import json

from villani_code.orchestrator_models import WorkerTask


def build_supervisor_prompt(objective: str, max_subtasks: int) -> str:
    contract = {
        "mode": "split",
        "subtasks": [
            {
                "id": "task_1",
                "goal": "Fix failing token refresh logic in auth middleware",
                "success_criteria": ["targeted auth tests pass"],
                "target_files": ["src/auth/middleware.py", "tests/test_auth.py"],
                "scope_hint": "Keep the patch minimal and avoid unrelated auth refactors.",
            }
        ],
    }
    direct = {"mode": "direct", "subtasks": []}
    return (
        "You are the supervisor planner for Villani orchestrator.\n"
        "Choose exactly one mode: direct or split.\n"
        f"If split, return at most {max_subtasks} bounded subtasks.\n"
        "If direct, return an empty subtasks list.\n"
        "Do not edit code. Output strict JSON only and no prose.\n"
        f"Objective: {objective}\n\n"
        f"Split JSON contract:\n{json.dumps(contract, indent=2)}\n\n"
        f"Direct JSON contract:\n{json.dumps(direct, indent=2)}"
    )


def build_worker_prompt(objective: str, task: WorkerTask, attempt: int, previous_failure: str | None = None) -> str:
    contract = {
        "status": "success",
        "summary": "Patched refresh expiry check and added regression coverage",
        "files_touched": ["src/auth/middleware.py", "tests/test_auth.py"],
        "recommended_verification": ["pytest tests/test_auth.py -q"],
    }
    prompt = [
        "You are a worker for Villani orchestrator.",
        "Handle exactly one scoped task.",
        "Prefer minimal patch.",
        "Stay within target files unless clearly forced.",
        "Stop when success criteria are satisfied or blocked.",
        "Output strict JSON only at the end.",
        "Allowed status values: success, blocked_environment, blocked_scope, failed.",
        f"Top-level objective: {objective}",
        f"Attempt: {attempt}",
        f"Task id: {task.id}",
        f"Task goal: {task.goal}",
        f"Success criteria: {task.success_criteria}",
        f"Target files: {task.target_files}",
        f"Scope hint: {task.scope_hint}",
    ]
    if previous_failure:
        prompt.append(f"Previous failure summary: {previous_failure}")
    prompt.append(f"JSON contract:\n{json.dumps(contract, indent=2)}")
    return "\n".join(prompt)
