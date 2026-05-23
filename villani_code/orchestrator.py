from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from villani_code.mission_state import get_current_mission_id, new_mission_id, set_current_mission_id
from villani_code.orchestrator_models import Subtask, SupervisorResult, WorkerResult
from villani_code.orchestrator_roles import build_supervisor_instruction, build_worker_instruction
from villani_code.orchestrator_verify import (
    capture_repo_file_state,
    cleanup_created_files,
    count_changed_lines,
    diff_repo_file_state,
    restore_files,
    run_verification,
    snapshot_files,
    to_json,
)
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
    max_worker_model_turns: int = 40
    max_worker_shell_commands: int = 20
    max_worker_changed_files: int = 5
    max_worker_changed_lines: int = 250


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
    retryable_failures_used = 0
    retryable_limit = 1

    for attempt in range(1, max_attempts + 1):
        prompt = build_worker_instruction(config.instruction, asdict(subtask), previous_failure=prev_summary)
        if result_path.exists():
            result_path.unlink()
        before_state = capture_repo_file_state(config.repo)
        try:
            proc = _run_child(
                instruction=prompt,
                inherited_run_args=config.inherited_run_args,
                mission_id=mission_id,
                role="worker",
                result_json_path=result_path,
                timeout_seconds=config.worker_timeout_seconds,
                repo=config.repo,
            )
            timeout_failure = False
        except subprocess.TimeoutExpired as exc:
            timeout_failure = True
            proc = subprocess.CompletedProcess(exc.cmd, returncode=124, stdout=str(exc.stdout or ""), stderr=str(exc.stderr or ""))
        after_state = capture_repo_file_state(config.repo)
        modified_files, created_files, deleted_files = diff_repo_file_state(before_state, after_state)
        actual_changed = sorted(set(modified_files + created_files + deleted_files))
        changed_line_count = count_changed_lines(config.repo, set(modified_files + created_files))
        out_of_scope_files = _out_of_scope_files(actual_changed, subtask.target_files)
        variant_files = _variant_sprawl_files(created_files, subtask.target_files)
        budget_reason = _budget_reason(
            proc=proc,
            changed_files_count=len(actual_changed),
            changed_line_count=changed_line_count,
            config=config,
        )
        worker_result = _load_worker_result(result_path)

        should_restore = False
        retryable = False
        failure_reasons: list[str] = []

        if timeout_failure:
            last_cmd = _extract_last_command(proc.stdout, proc.stderr)
            detail = f" (last command: {last_cmd})" if last_cmd else ""
            failure_reasons.append(f"worker timeout/stall detected{detail}")
            retryable = True
            should_restore = True
        elif proc.returncode != 0:
            failure_reasons.append(f"subprocess exited {proc.returncode}")
            should_restore = True
        elif worker_result is None:
            failure_reasons.append("invalid worker result")
            should_restore = True
        else:
            if out_of_scope_files:
                failure_reasons.append(f"out-of-scope edits: {', '.join(out_of_scope_files)}")
                retryable = worker_result.status == "blocked_scope"
                should_restore = True
            if variant_files:
                failure_reasons.append(f"variant/debug file sprawl: {', '.join(variant_files)}")
                should_restore = True
            if budget_reason:
                failure_reasons.append(budget_reason)
                retryable = True
                should_restore = True

            if not failure_reasons:
                verification = run_verification(
                    repo=config.repo,
                    worker_recommended=worker_result.recommended_verification,
                    success_criteria=subtask.success_criteria,
                    files_touched=actual_changed,
                    changed_line_count=changed_line_count,
                    max_files=config.max_worker_changed_files,
                    max_lines=config.max_worker_changed_lines,
                )
                (worker_dir / f"verification_attempt_{attempt}.json").write_text(
                    json.dumps(to_json(verification), indent=2), encoding="utf-8"
                )
                if verification.ok and worker_result.status == "success":
                    return {"success": True, "attempts": attempt}
                failure_reasons.append(worker_result.summary if worker_result.summary else "; ".join(verification.reasons))
                should_restore = True
        if should_restore:
            restore_files(config.repo, touched, snapshot_dir)
            cleanup_created_files(config.repo, out_of_scope_files)
        prev_summary = "; ".join(reason for reason in failure_reasons if reason)
        if prev_summary:
            (worker_dir / f"failure_attempt_{attempt}.json").write_text(
                json.dumps({"summary": prev_summary, "retryable": retryable}, indent=2), encoding="utf-8"
            )
        if retryable:
            retryable_failures_used += 1
        can_retry = attempt < max_attempts and (not retryable or retryable_failures_used <= retryable_limit)
        if not can_retry:
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


def _out_of_scope_files(changed_files: list[str], target_files: list[str]) -> list[str]:
    allowed = {path.strip() for path in target_files if path.strip()}
    return sorted(rel for rel in changed_files if rel not in allowed)


def _variant_sprawl_files(created_files: list[str], target_files: list[str]) -> list[str]:
    suffixes = ("_fixed", "_final", "_clean", "_new", "_v2", "_updated")
    debug_prefixes = ("debug_", "verify_", "fix_", "copy_")
    target_stems = {Path(rel).stem for rel in target_files}
    flagged: list[str] = []
    for rel in created_files:
        base = Path(rel).name
        stem = Path(rel).stem
        if any(base.startswith(prefix) for prefix in debug_prefixes):
            flagged.append(rel)
            continue
        for target_stem in target_stems:
            if any(stem == f"{target_stem}{suffix}" for suffix in suffixes):
                flagged.append(rel)
                break
    return sorted(set(flagged))


def _extract_metric(text: str, key: str) -> int | None:
    pattern = rf'"{re.escape(key)}"\s*:\s*(\d+)'
    match = re.findall(pattern, text)
    if not match:
        return None
    return int(match[-1])


def _budget_reason(
    proc: subprocess.CompletedProcess[str],
    changed_files_count: int,
    changed_line_count: int,
    config: OrchestratorConfig,
) -> str:
    stdout = str(proc.stdout or "")
    stderr = str(proc.stderr or "")
    merged = f"{stdout}\n{stderr}"
    turns = _extract_metric(merged, "turns_used")
    commands = _extract_metric(merged, "tool_calls_used")
    if turns is not None and turns > config.max_worker_model_turns:
        return f"budget exceeded: model turns {turns}>{config.max_worker_model_turns}"
    if commands is not None and commands > config.max_worker_shell_commands:
        return f"budget exceeded: shell commands {commands}>{config.max_worker_shell_commands}"
    if changed_files_count > config.max_worker_changed_files:
        return f"budget exceeded: changed files {changed_files_count}>{config.max_worker_changed_files}"
    if changed_line_count > config.max_worker_changed_lines:
        return f"budget exceeded: changed lines {changed_line_count}>{config.max_worker_changed_lines}"
    return ""


def _extract_last_command(stdout: str, stderr: str) -> str:
    merged = f"{stdout}\n{stderr}"
    matches = re.findall(r'"command"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', merged)
    if matches:
        return bytes(matches[-1], "utf-8").decode("unicode_escape")
    return ""
