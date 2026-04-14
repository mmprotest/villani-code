from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from villani_code.mission_state import create_mission_state, get_mission_dir, set_current_mission_id
from villani_code.orchestrator_git import commit_all, create_worktree, get_current_branch, get_head_commit, merge_branch
from villani_code.orchestrator_models import (
    OrchestratorState,
    SupervisorPlan,
    WorkerAttempt,
    WorkerRunRecord,
    WorkerTask,
    save_orchestrator_state,
    supervisor_plan_from_dict,
)
from villani_code.orchestrator_roles import build_supervisor_prompt, build_worker_prompt
from villani_code.orchestrator_verify import run_final_verification, verify_worker_result


def _validate_supervisor_plan(plan: SupervisorPlan, max_subtasks: int) -> bool:
    if plan.mode not in {"direct", "split"}:
        return False
    if plan.mode == "direct":
        return len(plan.subtasks) == 0
    if len(plan.subtasks) > max_subtasks:
        return False
    return all(task.id and task.goal for task in plan.subtasks)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def run_villani_subprocess(
    *,
    instruction: str,
    repo: Path,
    base_url: str,
    model: str,
    provider: str,
    api_key: str | None,
    max_tokens: int,
    small_model: bool,
    debug_mode: bool,
    debug_dir: Path | None,
    role: str,
    result_json_path: Path,
    parent_mission_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    run_dir = result_json_path.parent / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "villani_code.cli",
        "run",
        instruction,
        "--repo",
        str(repo),
        "--provider",
        provider,
        "--model",
        model,
        "--max-tokens",
        str(max_tokens),
        "--no-stream",
        "--role",
        role,
        "--result-json-path",
        str(result_json_path),
        "--parent-mission-id",
        parent_mission_id,
    ]
    if base_url:
        command.extend(["--base-url", base_url])
    if api_key:
        command.extend(["--api-key", api_key])
    if small_model:
        command.append("--small-model")
    if debug_mode:
        command.append("--debug")
    if debug_dir:
        command.extend(["--debug-dir", str(debug_dir)])

    try:
        proc = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False, timeout=timeout_seconds)
        return {
            "exit_code": proc.returncode,
            "run_dir": str(run_dir),
            "result_path": str(result_json_path),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": -1,
            "run_dir": str(run_dir),
            "result_path": str(result_json_path),
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
            "timed_out": True,
        }


def run_orchestrator(
    *,
    instruction: str,
    repo: Path,
    model: str,
    base_url: str,
    provider: str,
    api_key: str | None,
    max_tokens: int,
    small_model: bool,
    debug_mode: bool,
    debug_dir: Path | None,
    max_subtasks: int,
    max_worker_retries: int,
    supervisor_timeout_seconds: int,
    worker_timeout_seconds: int,
) -> dict[str, Any]:
    resolved_repo = repo.resolve()
    mission = create_mission_state(resolved_repo, instruction, mode="orchestrator")
    mission_id = mission.mission_id
    mission_dir = get_mission_dir(resolved_repo, mission_id)
    orch_dir = mission_dir / "orchestrator"
    state_path = orch_dir / "orchestrator_state.json"
    orch_dir.mkdir(parents=True, exist_ok=True)
    (orch_dir / "top_level_objective.txt").write_text(instruction, encoding="utf-8")

    state = OrchestratorState(
        mission_id=mission_id,
        objective=instruction,
        repo_root=str(resolved_repo),
        started_from_branch=get_current_branch(resolved_repo),
        base_commit=get_head_commit(resolved_repo),
        status="running",
    )
    save_orchestrator_state(state_path, state)

    supervisor_result_path = orch_dir / "supervisor" / "result.json"
    supervisor_prompt = build_supervisor_prompt(instruction, max_subtasks=max_subtasks)
    sup = run_villani_subprocess(
        instruction=supervisor_prompt,
        repo=resolved_repo,
        base_url=base_url,
        model=model,
        provider=provider,
        api_key=api_key,
        max_tokens=max_tokens,
        small_model=small_model,
        debug_mode=debug_mode,
        debug_dir=debug_dir,
        role="supervisor",
        result_json_path=supervisor_result_path,
        parent_mission_id=mission_id,
        timeout_seconds=supervisor_timeout_seconds,
    )
    state.supervisor_run_dir = str((orch_dir / "supervisor" / "run"))
    state.supervisor_result_json_path = str(supervisor_result_path)
    save_orchestrator_state(state_path, state)

    sup_payload = _load_json(supervisor_result_path)
    plan = supervisor_plan_from_dict((sup_payload or {}).get("response_json", {}))
    if not _validate_supervisor_plan(plan, max_subtasks=max_subtasks):
        retry_prompt = f"Return valid strict JSON only for this objective:\n{instruction}"
        _ = run_villani_subprocess(
            instruction=retry_prompt,
            repo=resolved_repo,
            base_url=base_url,
            model=model,
            provider=provider,
            api_key=api_key,
            max_tokens=max_tokens,
            small_model=small_model,
            debug_mode=debug_mode,
            debug_dir=debug_dir,
            role="supervisor",
            result_json_path=supervisor_result_path,
            parent_mission_id=mission_id,
            timeout_seconds=supervisor_timeout_seconds,
        )
        sup_payload = _load_json(supervisor_result_path)
        plan = supervisor_plan_from_dict((sup_payload or {}).get("response_json", {}))
        if not _validate_supervisor_plan(plan, max_subtasks=max_subtasks):
            state.status = "failed"
            state.final_summary = "Supervisor failed to produce a valid plan twice"
            save_orchestrator_state(state_path, state)
            return {"status": state.status, "summary": state.final_summary, "mission_id": mission_id}

    tasks = plan.subtasks if plan.mode == "split" else [
        WorkerTask(
            id="task_1",
            goal=instruction,
            success_criteria=[],
            target_files=[],
            scope_hint="",
        )
    ]

    accepted: list[WorkerRunRecord] = []
    for task in tasks:
        worktree_path, branch_name = create_worktree(resolved_repo, mission_dir, mission_id, task.id, state.base_commit)
        record = WorkerRunRecord(task_id=task.id, goal=task.goal, worktree_path=str(worktree_path), branch_name=branch_name)
        state.tasks.append(record)
        save_orchestrator_state(state_path, state)

        attempt_num = 1
        previous_failure: str | None = None
        while attempt_num <= (max_worker_retries + 1):
            worker_dir = orch_dir / "workers" / task.id / f"attempt_{attempt_num}"
            result_path = worker_dir / "result.json"
            verification_path = worker_dir / "verification.json"
            prompt = build_worker_prompt(instruction, task, attempt_num, previous_failure=previous_failure)
            run = run_villani_subprocess(
                instruction=prompt,
                repo=worktree_path,
                base_url=base_url,
                model=model,
                provider=provider,
                api_key=api_key,
                max_tokens=max_tokens,
                small_model=small_model,
                debug_mode=debug_mode,
                debug_dir=debug_dir,
                role="worker",
                result_json_path=result_path,
                parent_mission_id=mission_id,
                timeout_seconds=worker_timeout_seconds,
            )
            result_payload = _load_json(result_path) or {}
            response_payload = result_payload.get("response_json", {})
            recommended = response_payload.get("recommended_verification", []) if isinstance(response_payload, dict) else []
            if run["timed_out"]:
                verification = {"status": "retryable_failure", "summary": "Worker timed out", "commands_run": [], "files_touched": []}
            else:
                verification_obj = verify_worker_result(worktree_path, task, recommended if isinstance(recommended, list) else None)
                verification = {
                    "status": verification_obj.status,
                    "summary": verification_obj.summary,
                    "commands_run": verification_obj.commands_run,
                    "files_touched": verification_obj.files_touched,
                }
            verification_path.parent.mkdir(parents=True, exist_ok=True)
            verification_path.write_text(json.dumps(verification, indent=2), encoding="utf-8")

            attempt = WorkerAttempt(
                attempt=attempt_num,
                run_dir=run["run_dir"],
                result_json_path=run["result_path"],
                status=verification["status"],
                verification_summary=verification["summary"],
            )
            record.attempts.append(attempt)
            save_orchestrator_state(state_path, state)

            if verification["status"] == "accepted":
                committed = commit_all(worktree_path, f"orchestrator({task.id}): {task.goal[:72]}")
                if committed:
                    accepted.append(record)
                else:
                    record.merge_status = "failed"
                break

            previous_failure = verification["summary"]
            if verification["status"] != "retryable_failure" or attempt_num >= (max_worker_retries + 1):
                record.merge_status = "rejected"
                break
            attempt_num += 1

    merge_log: list[dict[str, Any]] = []
    merged_any = False
    for record in sorted(accepted, key=lambda item: item.task_id):
        ok, message = merge_branch(resolved_repo, record.branch_name)
        merge_log.append({"task_id": record.task_id, "branch_name": record.branch_name, "ok": ok, "message": message})
        record.merge_status = "merged" if ok else "merge_failed"
        save_orchestrator_state(state_path, state)
        if not ok:
            state.status = "failed"
            state.final_summary = f"Merge failed for {record.task_id}"
            (orch_dir / "merges").mkdir(parents=True, exist_ok=True)
            (orch_dir / "merges" / "merge_log.json").write_text(json.dumps(merge_log, indent=2), encoding="utf-8")
            save_orchestrator_state(state_path, state)
            return {"status": state.status, "summary": state.final_summary, "mission_id": mission_id}
        merged_any = True

    (orch_dir / "merges").mkdir(parents=True, exist_ok=True)
    (orch_dir / "merges" / "merge_log.json").write_text(json.dumps(merge_log, indent=2), encoding="utf-8")

    if merged_any:
        final = run_final_verification(resolved_repo)
        (orch_dir / "final_verification.json").write_text(
            json.dumps(
                {
                    "status": final.status,
                    "summary": final.summary,
                    "commands_run": final.commands_run,
                    "files_touched": final.files_touched,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        state.final_verification_status = final.status
        if final.status != "accepted":
            state.status = "failed"
            state.final_summary = "Final verification failed"
        else:
            state.status = "completed"
            state.final_summary = "Orchestration completed successfully"
    else:
        state.final_verification_status = "skipped"
        state.status = "failed"
        state.final_summary = "No worker changes accepted"

    set_current_mission_id(resolved_repo, mission_id)
    (orch_dir / "final_summary.json").write_text(
        json.dumps({"status": state.status, "summary": state.final_summary, "mission_id": mission_id}, indent=2),
        encoding="utf-8",
    )
    save_orchestrator_state(state_path, state)
    return {"status": state.status, "summary": state.final_summary, "mission_id": mission_id}
