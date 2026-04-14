from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SubagentLaunchRequest:
    role: str
    inherit_mission_state: bool
    objective: str
    target_files: list[str]
    known_facts: list[str]
    ruled_out: list[str]
    allowed_tools: list[str]
    write_allowed: bool
    require_verification_evidence: bool


def build_role_launch_request(role: str, objective: str, target_files: list[str] | None = None) -> SubagentLaunchRequest:
    files = list(target_files or [])
    if role == "fork_investigator":
        return SubagentLaunchRequest(role=role, inherit_mission_state=True, objective=objective, target_files=files, known_facts=[], ruled_out=[], allowed_tools=["Read", "Grep", "Search", "Bash"], write_allowed=False, require_verification_evidence=False)
    if role == "fresh_verifier":
        return SubagentLaunchRequest(role=role, inherit_mission_state=False, objective=objective, target_files=files, known_facts=[], ruled_out=[], allowed_tools=["Read", "Bash"], write_allowed=False, require_verification_evidence=True)
    if role == "bounded_patcher":
        return SubagentLaunchRequest(role=role, inherit_mission_state=True, objective=objective, target_files=files, known_facts=[], ruled_out=[], allowed_tools=["Read", "Patch", "Write", "Bash"], write_allowed=True, require_verification_evidence=True)
    if role == "supervisor":
        return SubagentLaunchRequest(role=role, inherit_mission_state=True, objective=objective, target_files=files, known_facts=[], ruled_out=[], allowed_tools=["Read", "Grep", "Search", "Bash"], write_allowed=False, require_verification_evidence=False)
    if role == "worker":
        return SubagentLaunchRequest(role=role, inherit_mission_state=True, objective=objective, target_files=files, known_facts=[], ruled_out=[], allowed_tools=["Read", "Patch", "Write", "Bash"], write_allowed=True, require_verification_evidence=True)
    return SubagentLaunchRequest(role="planner", inherit_mission_state=True, objective=objective, target_files=files, known_facts=[], ruled_out=[], allowed_tools=["Read", "Grep", "Search"], write_allowed=False, require_verification_evidence=False)


def render_subagent_brief(request: SubagentLaunchRequest) -> str:
    return "\n".join([
        f"Role: {request.role}",
        f"Objective: {request.objective}",
        f"Target files: {', '.join(request.target_files)}",
        f"Known facts: {'; '.join(request.known_facts)}",
        f"Ruled out: {'; '.join(request.ruled_out)}",
        f"Allowed tools: {', '.join(request.allowed_tools)}",
        f"Write allowed: {request.write_allowed}",
        f"Require verification evidence: {request.require_verification_evidence}",
    ])
