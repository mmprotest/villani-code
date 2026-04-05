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
        "active_artifacts": _filter_model_facing_paths(list(getattr(mission, "active_artifacts", []))),
        "superseded_artifacts": _filter_model_facing_paths(list(getattr(mission, "superseded_artifacts", []))),
        "final_targets": _filter_model_facing_paths(list(getattr(mission, "final_targets", []))),
        "environment_facts": list(getattr(mission, "environment_facts", [])),
        "last_validation_summary": getattr(mission, "last_validation_summary", ""),
        "last_validation_target": getattr(mission, "last_validation_target", ""),
        "attempted_strategies": list(getattr(mission, "attempted_strategies", [])),
        "compact_state_summary": getattr(mission, "compact_state_summary", ""),
        "compact_recent_actions": getattr(mission, "compact_summary", ""),
        "constraints": constraints,
        "repo_root": str(getattr(runner, "repo", "")),
        "skill_guidance": [s for s in skill_guidance if s][:8],
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
        f"Validation target: {packet.get('last_validation_target', '')}",
        f"Validation summary: {packet.get('last_validation_summary', '')}",
        f"Active artifacts: {', '.join(packet.get('active_artifacts', []))}",
        f"Superseded artifacts: {', '.join(packet.get('superseded_artifacts', []))}",
        f"Final targets: {', '.join(packet.get('final_targets', []))}",
        f"Environment facts: {' | '.join(packet.get('environment_facts', []))}",
        f"Attempted strategies: {' | '.join(packet.get('attempted_strategies', []))}",
        f"Compact state: {packet.get('compact_state_summary', '')}",
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
    return "\n".join(lines)
