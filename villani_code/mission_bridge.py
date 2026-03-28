from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.autonomy import contract_discourages_editing, contract_requires_validation
from villani_code.mission import Mission, MissionExecutionState, MissionNode


@dataclass(slots=True)
class MissionNodeResult:
    response: dict[str, Any]
    changed_files: list[str] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    prose_only: bool = False


def build_node_instruction(mission: Mission, node: MissionNode, execution_state: MissionExecutionState) -> str:
    lines = [
        f"MISSION OBJECTIVE: {mission.user_goal}",
        f"MISSION TYPE: {mission.mission_type.value}",
        f"NODE OBJECTIVE: {node.objective}",
        f"NODE PHASE: {node.phase.value}",
        f"TASK CONTRACT: {node.contract_type}",
    ]
    if node.candidate_files:
        lines.append("Candidate files: " + ", ".join(node.candidate_files[:12]))
    if node.evidence:
        lines.append("Known evidence: " + " | ".join(node.evidence[-6:]))
    if contract_discourages_editing(node.contract_type):
        lines.append("IMPORTANT: Editing is strongly discouraged unless absolutely necessary.")
    if contract_requires_validation(node.contract_type):
        plans = node.validation_commands or ["pytest -q"]
        lines.append("Validation plan: " + "; ".join(plans[:4]))
    lines.append("Stop when node objective is satisfied or clearly blocked. Provide concrete evidence.")
    return "\n".join(lines)


def _git_changed_files(repo: Path) -> list[str]:
    import subprocess

    proc = subprocess.run(["git", "diff", "--name-only"], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    return [x.strip() for x in proc.stdout.splitlines() if x.strip()]


def execute_mission_node_with_runner(
    runner: Any,
    mission: Mission,
    node: MissionNode,
    execution_state: MissionExecutionState,
) -> MissionNodeResult:
    instruction = build_node_instruction(mission, node, execution_state)
    before = set(_git_changed_files(Path(mission.repo_root)))
    result = runner.run(instruction)
    after = set(_git_changed_files(Path(mission.repo_root)))
    changed = sorted(after - before)

    transcript = (result or {}).get("transcript", {}) if isinstance(result, dict) else {}
    tool_results = list(transcript.get("tool_results", []) or [])
    commands: list[dict[str, Any]] = []
    failures: list[str] = []
    for tr in tool_results:
        content = str(tr.get("content", ""))
        if tr.get("is_error"):
            failures.append(content[:300])
        if "exit=" in content and "command" in content.lower():
            commands.append({"command": content[:160], "exit": 0 if "exit=0" in content else 1})
    text_blocks = (result.get("response", {}) or {}).get("content", []) if isinstance(result, dict) else []
    text = "\n".join(str(b.get("text", "")) for b in text_blocks if isinstance(b, dict))
    prose_only = (not changed) and ("```" not in text) and (len(tool_results) == 0)
    return MissionNodeResult(response=result, changed_files=changed, commands=commands, failures=failures, prose_only=prose_only)
