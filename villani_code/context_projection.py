from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from villani_code.state import Runner


def _is_runtime_artifact_path(path: str) -> bool:
    normalized = str(path).strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return False
    parts = [part for part in normalized.split("/") if part]
    return ".villani_code" in parts


def _filter_model_facing_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if not _is_runtime_artifact_path(path)]


def build_model_context_packet(runner: "Runner") -> dict[str, Any]:
    mission = getattr(runner, "_mission_state", None)
    constraints = []
    contract = getattr(runner, "_task_contract", {}) or {}
    if contract:
        constraints.append(f"Success predicate: {contract.get('success_predicate', '')}")
        constraints.extend([f"No-go: {p}" for p in contract.get("no_go_paths", [])[:4]])
    skill_guidance = [getattr(skill, "guidance", "") for skill in getattr(runner, "skills", []) if getattr(skill, "guidance", "")]
    recovery_mode = bool(getattr(runner, "_recovery_mode", False))
    primary_target = str(getattr(runner, "_primary_execution_target", "")).replace("\\", "/").lstrip("./")
    recovery_block: dict[str, Any] | None = None
    if recovery_mode and primary_target:
        failing_summary = str(getattr(runner, "_failing_error_summary", "") or "")
        failing_target_summary = str(getattr(runner, "_failing_target_contract_summary", "") or "")
        recovery_block = {
            "primary_execution_target": primary_target,
            "primary_execution_cwd": str(getattr(runner, "_primary_execution_target_cwd", "") or "."),
            "primary_execution_evidence": str(getattr(runner, "_primary_execution_target_evidence", "none")),
            "recovery_mode": True,
            "failing_target_summary": failing_target_summary,
            "failing_error_summary": failing_summary,
            "primary_target_minimally_valid": bool(getattr(runner, "_primary_target_minimally_valid", False)),
            "switch_blocked": bool(getattr(runner, "_recovery_target_switch_blocked", False)),
            "active_solution_last_validation_ok": getattr(runner, "_active_solution_last_validation_ok", None),
            "needs_direct_validation": not bool(getattr(runner, "_primary_target_minimally_valid", False)),
        }
    return {
        "objective": getattr(mission, "objective", ""),
        "runtime_mode": getattr(mission, "mode", getattr(runner, "_runtime_mode", "execution")),
        "current_step": getattr(mission, "current_step_id", ""),
        "plan_summary": getattr(mission, "plan_summary", ""),
        "verified_facts": [f.value for f in getattr(mission, "verified_facts", [])],
        "open_hypotheses": [h.statement for h in getattr(mission, "open_hypotheses", [])],
        "intended_targets": _filter_model_facing_paths(list(getattr(mission, "intended_targets", []))),
        "changed_files": _filter_model_facing_paths(list(getattr(mission, "changed_files", []))),
        "last_failed_command": getattr(mission, "last_failed_command", ""),
        "validation_failures": list(getattr(mission, "validation_failures", [])),
        "compact_recent_actions": getattr(mission, "compact_summary", ""),
        "constraints": constraints,
        "repo_root": str(getattr(runner, "repo", "")),
        "skill_guidance": [s for s in skill_guidance if s][:8],
        "recovery": recovery_block,
    }


def render_model_context_packet(packet: dict[str, Any]) -> str:
    lines = [
        "Mission context packet:",
        f"Objective: {packet.get('objective', '')}",
        f"Mode: {packet.get('runtime_mode', '')}",
        f"Current step: {packet.get('current_step', '')}",
        f"Plan summary: {packet.get('plan_summary', '')}",
        f"Intended targets: {', '.join(packet.get('intended_targets', []))}",
        f"Changed files: {', '.join(packet.get('changed_files', []))}",
        f"Last failed command: {packet.get('last_failed_command', '')}",
        f"Validation failures: {' | '.join(packet.get('validation_failures', []))}",
        f"Compact actions: {packet.get('compact_recent_actions', '')}",
    ]
    constraints = packet.get("constraints", [])
    if constraints:
        lines.append("Constraints:")
        lines.extend(f"- {c}" for c in constraints[:8])
    guidance = packet.get("skill_guidance", [])
    if guidance:
        lines.append("Skill guidance:")
        lines.extend(f"- {g}" for g in guidance[:6])
    recovery = packet.get("recovery", {})
    if isinstance(recovery, dict) and recovery:
        failure_summary = str(recovery.get("failing_error_summary", "") or recovery.get("failing_target_summary", ""))
        lines.append(
            "Recovery contract: "
            f"target={recovery.get('primary_execution_target', '')}; "
            f"cwd={recovery.get('primary_execution_cwd', '.')}; "
            f"evidence={recovery.get('primary_execution_evidence', 'none')}; "
            f"mode={bool(recovery.get('recovery_mode', False))}; "
            f"min_valid={bool(recovery.get('primary_target_minimally_valid', False))}; "
            f"switch_blocked={bool(recovery.get('switch_blocked', False))}; "
            f"active_validation_ok={recovery.get('active_solution_last_validation_ok', None)}; "
            f"needs_direct_validation={bool(recovery.get('needs_direct_validation', True))}; "
            f"failure={failure_summary}"
        )
    return "\n".join(lines)
