from __future__ import annotations

import json
import subprocess
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from villani_code.orchestrate.merge import apply_diff, score_candidate
from villani_code.orchestrate.prompts import build_patch_prompt, build_scout_prompt
from villani_code.orchestrate.state import CandidatePatch, OrchestrateState, PatchUnit
from villani_code.orchestrate.verify import detect_verify_commands, run_verification
from villani_code.orchestrate.worker import WorkerConfig, WorkerRunResult, run_worker
from villani_code.orchestrate.worktree import WorkspaceManager, git_changed_files, git_diff_text, is_dirty


def _extract_paths(values: list[str]) -> list[str]:
    hits: list[str] = []
    for value in values:
        if "/" in value or "." in value:
            hits.append(value)
    return hits


def _plan_units(state: OrchestrateState, max_units: int = 3) -> list[PatchUnit]:
    mentions = Counter(state.files_in_scope + state.current_frontier)
    top_files = [file for file, _ in mentions.most_common(max_units)]
    if not top_files:
        top_files = ["(determine best target file from latest evidence)"]
    units: list[PatchUnit] = []
    for idx, target in enumerate(top_files[:max_units], start=1):
        units.append(
            PatchUnit(
                title=f"Patch unit {idx}",
                objective=f"Implement a minimal fix related to {target}",
                target_files=[target],
                evidence=[{"claim": "Repeatedly mentioned by scouts", "source": target}],
                constraints=state.constraints,
            )
        )
    return units


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def orchestrate(
    *,
    task: str,
    repo: Path,
    base_url: str,
    model: str,
    provider: str,
    api_key: str | None,
    workers: int,
    scout_workers: int,
    patch_workers: int,
    rounds: int,
    worker_timeout: int,
    verify_command: str | None,
    output_dir: Path | None,
    keep_worktrees: bool,
    worker_runner: Callable[..., WorkerRunResult] = run_worker,
) -> dict[str, object]:
    run_id = uuid.uuid4().hex[:12]
    artifacts_root = (output_dir or (repo / "artifacts" / "orchestrate" / run_id)).resolve()
    artifacts_root.mkdir(parents=True, exist_ok=True)

    state = OrchestrateState(
        original_task=task,
        success_criteria=["verification command passes"],
        constraints=["minimal coherent patch", "no broad rewrites", "single-round worker attempt"],
    )
    state_path = artifacts_root / "state.json"
    state.save(state_path)

    config = WorkerConfig(base_url=base_url, model=model, provider=provider, api_key=api_key, timeout_seconds=worker_timeout)
    workspace_manager = WorkspaceManager(repo=repo, keep_worktrees=keep_worktrees)
    verify_cmd = verify_command or detect_verify_commands(repo)[0]

    stop_reason = "round_limit_reached"
    merged_files: list[str] = []

    for round_index in range(1, rounds + 1):
        round_dir = artifacts_root / f"round_{round_index}"
        round_dir.mkdir(parents=True, exist_ok=True)

        scout_reports = []
        for scout_index in range(1, min(workers, scout_workers) + 1):
            workspace = workspace_manager.create(f"scout-r{round_index}-{scout_index}")
            prompt = build_scout_prompt(state.to_dict(), f"Scout pass {scout_index} for task: {task}")
            result = worker_runner(repo=workspace.path, prompt=prompt, config=config)
            scout_log = round_dir / f"scout_{scout_index}_output.txt"
            scout_log.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")

            dirty = is_dirty(workspace.path)
            if dirty:
                result.report.status = "failed"
                result.report.summary = "Scout modified files; discarded workspace"
            scout_reports.append(result.report)
            _write_json(round_dir / f"scout_{scout_index}_report.json", asdict(result.report))
            workspace_manager.cleanup(workspace)

        for report in scout_reports:
            state.files_in_scope.extend(report.likely_files)
            state.files_in_scope.extend(report.files_read)
            state.current_frontier.extend(report.hypotheses)
            state.repo_facts.extend([item.get("claim", "") for item in report.evidence if isinstance(item, dict)])

        patch_units = _plan_units(state)
        _write_json(round_dir / "plan_units.json", {"patch_units": [asdict(unit) for unit in patch_units]})

        candidates: list[CandidatePatch] = []
        for patch_index in range(1, min(patch_workers, len(patch_units)) + 1):
            unit = patch_units[patch_index - 1]
            workspace = workspace_manager.create(f"patch-r{round_index}-{patch_index}")
            prompt = build_patch_prompt(state.to_dict(), unit)
            result = worker_runner(repo=workspace.path, prompt=prompt, config=config)

            diff_text = git_diff_text(workspace.path)
            changed = git_changed_files(workspace.path)
            diff_path = round_dir / f"patch_{patch_index}.diff"
            diff_path.write_text(diff_text, encoding="utf-8")
            _write_json(round_dir / f"patch_{patch_index}_report.json", asdict(result.report))
            (round_dir / f"patch_{patch_index}_output.txt").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")

            candidates.append(
                CandidatePatch(
                    worker_id=f"round{round_index}-patch{patch_index}",
                    patch_unit=unit,
                    report=result.report,
                    diff_path=diff_path,
                    diff_text=diff_text,
                    files_changed=changed,
                )
            )
            workspace_manager.cleanup(workspace)

        scored: list[tuple[tuple[int, int, int, int, int, int], CandidatePatch, dict[str, object]]] = []
        evidence_files = set(state.files_in_scope)
        for candidate in candidates:
            verify_workspace = workspace_manager.create(f"verify-r{round_index}-{candidate.worker_id}")
            verified = False
            verify_payload: dict[str, object] = {"command": verify_cmd, "passed": False}
            if candidate.diff_text.strip() and apply_diff(verify_workspace.path, candidate.diff_text):
                verified, verify_payload = run_verification(
                    verify_workspace.path,
                    verify_cmd,
                    worker_timeout,
                    round_dir / "verification_logs",
                )
            else:
                verify_payload = {"command": verify_cmd, "passed": False, "error": "diff_apply_failed_or_empty"}
            score = score_candidate(candidate, evidence_files, bool(verify_payload.get("passed", False)))
            scored.append((score, candidate, verify_payload))
            state.verification_history.append(
                {
                    "round": round_index,
                    "worker_id": candidate.worker_id,
                    "score": list(score),
                    "verification": verify_payload,
                }
            )
            workspace_manager.cleanup(verify_workspace)

        scored.sort(key=lambda item: item[0], reverse=True)
        winning = scored[0] if scored else None
        if winning and bool(winning[2].get("passed", False)):
            winner = winning[1]
            if apply_diff(repo, winner.diff_text):
                merged_files = winner.files_changed
                state.merged_patches.append(
                    {
                        "round": round_index,
                        "worker_id": winner.worker_id,
                        "files": winner.files_changed,
                        "diff_path": str(winner.diff_path) if winner.diff_path else "",
                    }
                )
                state.completed_rounds = round_index
                final_pass, final_verify = run_verification(repo, verify_cmd, worker_timeout, round_dir / "final_verification")
                state.verification_history.append({"round": round_index, "scope": "final", "verification": final_verify})
                if final_pass:
                    stop_reason = "success"
                    break
                stop_reason = "merged_candidate_failed_final_verification"
            else:
                state.attempts.append({"round": round_index, "reason": "winner_diff_failed_apply_in_repo"})
        else:
            state.attempts.append({"round": round_index, "reason": "no_passing_candidate"})
        state.completed_rounds = round_index
        state.save(state_path)

    state.stop_reason = stop_reason
    state.save(state_path)

    changed_after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=False).stdout
    final_report = {
        "run_id": run_id,
        "task": task,
        "stop_reason": stop_reason,
        "verification_passed": stop_reason == "success",
        "merged_patches": state.merged_patches,
        "files_changed": merged_files,
        "commands_run": [verify_cmd],
        "failed_attempts": state.attempts,
        "remaining_risks": [] if stop_reason == "success" else ["Verification did not pass with available candidates"],
        "repo_status": changed_after.splitlines(),
    }
    _write_json(artifacts_root / "final_report.json", final_report)
    (artifacts_root / "summary.md").write_text(
        "\n".join(
            [
                f"# Orchestrate summary ({run_id})",
                f"- task: {task}",
                f"- stop_reason: {stop_reason}",
                f"- verification_passed: {str(final_report['verification_passed']).lower()}",
                f"- merged_patches: {len(state.merged_patches)}",
                f"- files_changed: {', '.join(merged_files) if merged_files else '(none)'}",
                f"- commands_run: {verify_cmd}",
                f"- failed_attempts: {len(state.attempts)}",
                f"- remaining_risks: {', '.join(final_report['remaining_risks']) if final_report['remaining_risks'] else '(none)'}",
            ]
        ) + "\n",
        encoding="utf-8",
    )
    return final_report
