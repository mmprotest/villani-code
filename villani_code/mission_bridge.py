from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.autonomy import contract_discourages_editing, contract_requires_validation
from villani_code.evidence import parse_command_evidence
from villani_code.mission import Mission, MissionExecutionState, MissionNode


@dataclass(slots=True)
class CommandResult:
    command: str
    exit: int
    stdout: str = ""
    stderr: str = ""
    source: str = "unknown"
    timed_out: bool = False


@dataclass(slots=True)
class MissionNodeResult:
    response: dict[str, Any]
    changed_files: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    command_results: list[CommandResult] = field(default_factory=list)
    tool_failures: list[str] = field(default_factory=list)
    patch_detected: bool = False
    meaningful_patch: bool = False
    transcript_summary: str = ""
    model_activity: dict[str, int] = field(default_factory=dict)
    prose_only: bool = False
    acted: bool = False

    @property
    def commands(self) -> list[dict[str, Any]]:
        return [
            {
                "command": c.command,
                "exit": c.exit,
                "stdout": c.stdout,
                "stderr": c.stderr,
                "source": c.source,
                "timed_out": c.timed_out,
            }
            for c in self.command_results
        ]

    @property
    def failures(self) -> list[str]:
        return list(self.tool_failures)


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
    if execution_state.last_localization.target_files:
        lines.append("Localized targets: " + ", ".join(execution_state.last_localization.target_files[:12]))
        lines.append(f"Localized bug class: {execution_state.last_localization.likely_bug_class}")
        if execution_state.last_localization.repair_intent:
            lines.append("Localized repair intent: " + execution_state.last_localization.repair_intent)
    if node.evidence:
        lines.append("Known evidence: " + " | ".join(node.evidence[-6:]))
    if contract_discourages_editing(node.contract_type):
        lines.append("IMPORTANT: Editing is strongly discouraged unless absolutely necessary.")
    if contract_requires_validation(node.contract_type):
        plans = list(node.validation_commands)
        if execution_state.last_localization.suggested_validation_commands:
            plans = execution_state.last_localization.suggested_validation_commands + plans
        plans = list(dict.fromkeys([p for p in plans if p])) or ["pytest -q"]
        lines.append("Validation plan: " + "; ".join(plans[:4]))
    lines.append("Stop when node objective is satisfied or clearly blocked. Provide concrete evidence.")
    return "\n".join(lines)


def _git_changed_files(repo: Path) -> list[str]:
    import subprocess

    proc = subprocess.run(["git", "diff", "--name-only"], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    return [x.strip() for x in proc.stdout.splitlines() if x.strip()]


def _extract_execution_payload(result: dict[str, Any]) -> dict[str, Any]:
    execution = result.get("execution")
    if isinstance(execution, dict):
        return execution
    transcript = result.get("transcript", {}) if isinstance(result.get("transcript"), dict) else {}
    nested = transcript.get("execution")
    return nested if isinstance(nested, dict) else {}


def _parse_command_result_content(content: Any) -> list[CommandResult]:
    records: list[CommandResult] = []
    text = str(content or "")
    try:
        decoded = json.loads(text)
    except Exception:
        decoded = None
    if isinstance(decoded, dict) and "command" in decoded:
        cmd = str(decoded.get("command", "")).strip()
        if cmd:
            try:
                exit_code = int(decoded.get("exit_code", decoded.get("exit", 1)) or 1)
            except (TypeError, ValueError):
                exit_code = 1
            records.append(
                CommandResult(
                    command=cmd,
                    exit=exit_code,
                    stdout=str(decoded.get("stdout", "") or "")[:4000],
                    stderr=str(decoded.get("stderr", "") or "")[:4000],
                    source="tool_result_json",
                )
            )
            return records
    for rec in parse_command_evidence(text):
        cmd = str(rec.get("command", "")).strip()
        if not cmd:
            continue
        records.append(CommandResult(command=cmd, exit=int(rec.get("exit", 1)), source="tool_result_text"))
    return records


def _extract_tool_data(result: dict[str, Any]) -> tuple[list[CommandResult], list[str], dict[str, int]]:
    transcript = result.get("transcript", {}) if isinstance(result.get("transcript"), dict) else {}
    tool_results = list(transcript.get("tool_results", []) or [])
    tool_invocations = list(transcript.get("tool_invocations", []) or [])
    commands: list[CommandResult] = []
    failures: list[str] = []
    model_activity = {
        "tool_invocations": len(tool_invocations),
        "tool_results": len(tool_results),
        "tool_errors": 0,
        "responses": len(list(transcript.get("responses", []) or [])),
        "requests": len(list(transcript.get("requests", []) or [])),
    }
    for idx, tr in enumerate(tool_results):
        tr = tr if isinstance(tr, dict) else {}
        inv = tool_invocations[idx] if idx < len(tool_invocations) and isinstance(tool_invocations[idx], dict) else {}
        is_error = bool(tr.get("is_error"))
        if is_error:
            model_activity["tool_errors"] += 1
            failures.append(str(tr.get("content", ""))[:320])
        tool_name = str(inv.get("name", "")).lower()
        if tool_name == "bash" or "exit_code" in str(tr.get("content", "")):
            parsed = _parse_command_result_content(tr.get("content", ""))
            commands.extend(parsed)
    dedup: dict[tuple[str, int], CommandResult] = {}
    for record in commands:
        dedup[(record.command, record.exit)] = record
    return list(dedup.values()), failures, model_activity


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

    normalized = result if isinstance(result, dict) else {}
    execution = _extract_execution_payload(normalized)
    execution_commands = list(execution.get("validation_artifacts", []) or [])
    command_results, failures, model_activity = _extract_tool_data(normalized)
    commands_run = [c.command for c in command_results]
    if not commands_run:
        commands_run = [str(x).split(" (exit=", 1)[0] for x in execution_commands if str(x).strip()]
    text_blocks = (normalized.get("response", {}) or {}).get("content", []) if isinstance(normalized, dict) else []
    text = "\n".join(str(b.get("text", "")) for b in text_blocks if isinstance(b, dict))
    prose_only = (not changed) and (not command_results) and model_activity.get("tool_results", 0) == 0 and bool(text.strip())
    patch_detected = bool(changed) or bool(execution.get("files_changed"))
    meaningful_patch = bool(execution.get("intentional_changes")) or bool(changed)
    acted = patch_detected or bool(command_results) or model_activity.get("tool_invocations", 0) > 0
    transcript_summary = (
        f"tools={model_activity.get('tool_results', 0)} "
        f"errors={model_activity.get('tool_errors', 0)} "
        f"commands={len(command_results)} changed={len(changed)}"
    )
    return MissionNodeResult(
        response=normalized,
        changed_files=changed,
        commands_run=commands_run,
        command_results=command_results,
        tool_failures=failures,
        patch_detected=patch_detected,
        meaningful_patch=meaningful_patch,
        transcript_summary=transcript_summary,
        model_activity=model_activity,
        prose_only=prose_only,
        acted=acted,
    )
