from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from villani_code.mission_state import get_current_mission_id, new_mission_id, set_current_mission_id
from villani_code.orchestrator_models import Subtask, SupervisorResult, WorkerResult
from villani_code.orchestrator_roles import build_supervisor_instruction, build_worker_instruction
from villani_code.orchestrator_verify import restore_files, run_verification, snapshot_files, to_json
from villani_code.utils import ensure_dir


@dataclass(slots=True)
class OrchestratorConfig:
    instruction: str
    repo: Path
    inherited_run_args: list[str]
    max_workers: int = 3
    max_worker_retries: int = 1
    supervisor_timeout_seconds: int | None = None
    worker_timeout_seconds: int | None = None


@dataclass(slots=True)
class OrchestratorState:
    mission_id: str
    objective: str
    supervisor_attempts: int = 0
    worker_attempts: dict[str, int] = field(default_factory=dict)
    successful_workers: list[str] = field(default_factory=list)


def run_orchestrator(config: OrchestratorConfig) -> dict[str, Any]:
    repo = config.repo.resolve()
    mission_id = new_mission_id()
    base = repo / ".villani_code" / "missions" / mission_id / "orchestrator"
    supervisor_dir = base / "supervisor"
    workers_dir = base / "workers"
    snapshots_dir = base / "snapshots"
    for path in (base, supervisor_dir, workers_dir, snapshots_dir):
        ensure_dir(path)
    (base / "top_level_objective.txt").write_text(config.instruction, encoding="utf-8")

    state = OrchestratorState(mission_id=mission_id, objective=config.instruction)
    _save_state(base, state)

    parent_mission_id = get_current_mission_id(repo)
    if parent_mission_id:
        set_current_mission_id(repo, parent_mission_id)

    supervisor = _run_supervisor(config, mission_id, supervisor_dir)
    state.supervisor_attempts = supervisor["attempts"]
    _save_state(base, state)

    subtasks = supervisor["result"].subtasks
    success_count = 0
    for subtask in subtasks:
        worker_record = _run_worker_with_retries(
            config=config,
            mission_id=mission_id,
            subtask=subtask,
            workers_dir=workers_dir,
            snapshots_dir=snapshots_dir,
        )
        state.worker_attempts[subtask.id] = int(worker_record.get("attempts", 0))
        if worker_record.get("success"):
            success_count += 1
            state.successful_workers.append(subtask.id)
        _save_state(base, state)

    final_verification = {
        "ran": success_count > 0,
        "ok": success_count > 0,
        "successful_workers": success_count,
    }
    (base / "final_verification.json").write_text(json.dumps(final_verification, indent=2), encoding="utf-8")

    summary = {
        "mission_id": mission_id,
        "status": "success" if success_count > 0 else "failed",
        "successful_workers": success_count,
        "total_subtasks": len(subtasks),
    }
    (base / "final_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _save_state(base, state)
    if parent_mission_id:
        set_current_mission_id(repo, parent_mission_id)
    return summary


def _save_state(base: Path, state: OrchestratorState) -> None:
    (base / "orchestrator_state.json").write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def _run_supervisor(config: OrchestratorConfig, mission_id: str, supervisor_dir: Path) -> dict[str, Any]:
    result_path = supervisor_dir / "result.json"
    prompt = build_supervisor_instruction(config.instruction, config.max_workers)
    last_error = "supervisor failed"
    for attempt in range(1, 3):
        if result_path.exists():
            result_path.unlink()
        proc = _run_child(
            instruction=prompt,
            inherited_run_args=config.inherited_run_args,
            mission_id=mission_id,
            role="supervisor",
            result_json_path=result_path,
            timeout_seconds=config.supervisor_timeout_seconds,
            repo=config.repo,
        )
        if proc.returncode != 0:
            last_error = f"supervisor subprocess exited {proc.returncode}"
            continue
        loaded = _load_supervisor_result(result_path, config.max_workers)
        if loaded is None:
            last_error = "invalid supervisor result"
            continue
        return {"result": loaded, "attempts": attempt}
    raise RuntimeError(last_error)


def _run_worker_with_retries(
    config: OrchestratorConfig,
    mission_id: str,
    subtask: Subtask,
    workers_dir: Path,
    snapshots_dir: Path,
) -> dict[str, Any]:
    worker_dir = workers_dir / subtask.id
    ensure_dir(worker_dir)
    result_path = worker_dir / "result.json"
    prev_summary: str | None = None
    touched = sorted(set(subtask.target_files))
    snapshot_dir = snapshots_dir / subtask.id
    snapshot_files(config.repo, touched, snapshot_dir)
    max_attempts = config.max_worker_retries + 1

    for attempt in range(1, max_attempts + 1):
        prompt = build_worker_instruction(config.instruction, asdict(subtask), previous_failure=prev_summary)
        if result_path.exists():
            result_path.unlink()
        proc = _run_child(
            instruction=prompt,
            inherited_run_args=config.inherited_run_args,
            mission_id=mission_id,
            role="worker",
            result_json_path=result_path,
            timeout_seconds=config.worker_timeout_seconds,
            repo=config.repo,
        )
        if proc.returncode != 0:
            prev_summary = f"subprocess exited {proc.returncode}"
            continue

        worker_result = _load_worker_result(result_path)
        if worker_result is None:
            prev_summary = "invalid worker result"
            continue

        changed = _count_changed_lines(config.repo, worker_result.files_touched)
        verification = run_verification(
            repo=config.repo,
            worker_recommended=worker_result.recommended_verification,
            success_criteria=subtask.success_criteria,
            files_touched=worker_result.files_touched,
            changed_line_count=changed,
        )
        (worker_dir / f"verification_attempt_{attempt}.json").write_text(
            json.dumps(to_json(verification), indent=2), encoding="utf-8"
        )
        if verification.ok and worker_result.status == "success":
            return {"success": True, "attempts": attempt}

        restore_files(config.repo, touched, snapshot_dir)
        prev_summary = worker_result.summary if worker_result.summary else "; ".join(verification.reasons)
        if attempt == max_attempts:
            return {"success": False, "attempts": attempt}

    return {"success": False, "attempts": max_attempts}


def _run_child(
    instruction: str,
    inherited_run_args: list[str],
    mission_id: str,
    role: str,
    result_json_path: Path,
    timeout_seconds: int | None,
    repo: Path,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "villani_code.cli",
        "run",
        instruction,
        *inherited_run_args,
        "--role",
        role,
        "--result-json-path",
        str(result_json_path),
        "--parent-mission-id",
        mission_id,
    ]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout_seconds)


def _load_supervisor_result(path: Path, max_workers: int) -> SupervisorResult | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    subtasks_raw = payload.get("subtasks")
    if not isinstance(subtasks_raw, list) or not (1 <= len(subtasks_raw) <= max_workers):
        return None
    subtasks: list[Subtask] = []
    for i, item in enumerate(subtasks_raw, start=1):
        if not isinstance(item, dict):
            return None
        goal = str(item.get("goal", "")).strip()
        if not goal:
            return None
        subtasks.append(
            Subtask(
                id=str(item.get("id") or f"task_{i}"),
                goal=goal,
                success_criteria=[str(v) for v in item.get("success_criteria", []) if str(v).strip()],
                target_files=[str(v) for v in item.get("target_files", []) if str(v).strip()],
                scope_hint=str(item.get("scope_hint", "")),
            )
        )
    return SupervisorResult(subtasks=subtasks)


def _load_worker_result(path: Path) -> WorkerResult | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    status = str(payload.get("status", ""))
    if status not in {"success", "blocked_environment", "blocked_scope", "failed"}:
        return None
    return WorkerResult(
        status=status,  # type: ignore[arg-type]
        summary=str(payload.get("summary", "")),
        files_touched=[str(v) for v in payload.get("files_touched", []) if str(v).strip()],
        recommended_verification=[str(v) for v in payload.get("recommended_verification", []) if str(v).strip()],
    )


def _count_changed_lines(repo: Path, files: list[str]) -> int:
    changed = 0
    for rel in files:
        path = repo / rel
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        changed += len(text.splitlines())
    return changed
