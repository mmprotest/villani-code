from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class WorkerTask:
    id: str
    goal: str
    success_criteria: list[str]
    target_files: list[str]
    scope_hint: str = ""


@dataclass(slots=True)
class SupervisorPlan:
    mode: str
    subtasks: list[WorkerTask]


@dataclass(slots=True)
class WorkerAttempt:
    attempt: int
    run_dir: str
    result_json_path: str
    status: str
    verification_summary: str = ""


@dataclass(slots=True)
class WorkerRunRecord:
    task_id: str
    goal: str
    worktree_path: str
    branch_name: str
    attempts: list[WorkerAttempt] = field(default_factory=list)
    merge_status: str = "pending"


@dataclass(slots=True)
class VerificationResult:
    status: str
    summary: str
    commands_run: list[str]
    files_touched: list[str]


@dataclass(slots=True)
class OrchestratorState:
    mission_id: str
    objective: str
    repo_root: str
    started_from_branch: str
    base_commit: str
    status: str
    supervisor_run_dir: str = ""
    supervisor_result_json_path: str = ""
    tasks: list[WorkerRunRecord] = field(default_factory=list)
    final_verification_status: str = ""
    final_summary: str = ""


def _worker_task_from_dict(payload: dict[str, Any]) -> WorkerTask:
    return WorkerTask(
        id=str(payload.get("id", "")),
        goal=str(payload.get("goal", "")),
        success_criteria=[str(v) for v in payload.get("success_criteria", [])],
        target_files=[str(v) for v in payload.get("target_files", [])],
        scope_hint=str(payload.get("scope_hint", "")),
    )


def supervisor_plan_from_dict(payload: dict[str, Any]) -> SupervisorPlan:
    return SupervisorPlan(
        mode=str(payload.get("mode", "")),
        subtasks=[_worker_task_from_dict(item) for item in payload.get("subtasks", []) if isinstance(item, dict)],
    )


def orchestrator_state_to_dict(state: OrchestratorState) -> dict[str, Any]:
    return asdict(state)


def orchestrator_state_from_dict(payload: dict[str, Any]) -> OrchestratorState:
    tasks: list[WorkerRunRecord] = []
    for item in payload.get("tasks", []):
        if not isinstance(item, dict):
            continue
        attempts = [
            WorkerAttempt(
                attempt=int(attempt.get("attempt", 0)),
                run_dir=str(attempt.get("run_dir", "")),
                result_json_path=str(attempt.get("result_json_path", "")),
                status=str(attempt.get("status", "")),
                verification_summary=str(attempt.get("verification_summary", "")),
            )
            for attempt in item.get("attempts", [])
            if isinstance(attempt, dict)
        ]
        tasks.append(
            WorkerRunRecord(
                task_id=str(item.get("task_id", "")),
                goal=str(item.get("goal", "")),
                worktree_path=str(item.get("worktree_path", "")),
                branch_name=str(item.get("branch_name", "")),
                attempts=attempts,
                merge_status=str(item.get("merge_status", "pending")),
            )
        )

    return OrchestratorState(
        mission_id=str(payload.get("mission_id", "")),
        objective=str(payload.get("objective", "")),
        repo_root=str(payload.get("repo_root", "")),
        started_from_branch=str(payload.get("started_from_branch", "")),
        base_commit=str(payload.get("base_commit", "")),
        status=str(payload.get("status", "pending")),
        supervisor_run_dir=str(payload.get("supervisor_run_dir", "")),
        supervisor_result_json_path=str(payload.get("supervisor_result_json_path", "")),
        tasks=tasks,
        final_verification_status=str(payload.get("final_verification_status", "")),
        final_summary=str(payload.get("final_summary", "")),
    )


def save_orchestrator_state(path: Path, state: OrchestratorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(orchestrator_state_to_dict(state), indent=2), encoding="utf-8")


def load_orchestrator_state(path: Path) -> OrchestratorState:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid orchestrator state payload")
    return orchestrator_state_from_dict(payload)
