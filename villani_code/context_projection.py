from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from villani_code.state import Runner


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
        "intended_targets": list(getattr(mission, "intended_targets", [])),
        "changed_files": list(getattr(mission, "changed_files", [])),
        "last_failed_command": getattr(mission, "last_failed_command", ""),
        "validation_failures": list(getattr(mission, "validation_failures", [])),
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
