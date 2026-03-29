from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
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
    clarification_requested: bool = False
    execution_payload: dict[str, Any] = field(default_factory=dict)

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
    if execution_state.greenfield_selection and mission.mission_type.value == "greenfield_build":
        lines.append("Chosen project direction: " + str(execution_state.greenfield_selection.get("project_type", "")))
    if contract_discourages_editing(node.contract_type):
        lines.append("IMPORTANT: Editing is strongly discouraged unless absolutely necessary.")
    if contract_requires_validation(node.contract_type):
        plans = list(node.validation_commands)
        if execution_state.last_localization.suggested_validation_commands:
            plans = execution_state.last_localization.suggested_validation_commands + plans
        plans = list(dict.fromkeys([p for p in plans if p])) or ["pytest -q"]
        lines.append("Validation plan: " + "; ".join(plans[:4]))
    if mission.mission_type.value == "greenfield_build":
        lines.append("GREENFIELD RULES: Build a real runnable deliverable in user workspace paths.")
        lines.append("Do not treat this as bugfix/localization-first work unless a build-generated bug appears.")
        lines.append("Files under .villani/ are internal artifacts only and do not count as project deliverables.")
        lines.append("Do NOT ask the user for confirmation/approval/options. Act autonomously unless a true hard block exists.")
        if node.phase.value == "inspect_workspace":
            lines.append("Inspect workspace for constraints, sample data, README/notes hints, and feasible local project directions.")
        elif node.phase.value == "choose_project_direction":
            lines.append("Produce 2-4 plausible runnable utility candidates, then choose one deterministic direction with rationale.")
            lines.append("Docs-only, README-only, and suggestion-only directions are invalid unless the user explicitly asked for docs only.")
        elif node.phase.value == "scaffold_project":
            lines.append("Scaffold only the chosen project structure in user-facing paths. Avoid .villani/ outputs.")
            lines.append("This phase must create at least one real user-space file (for example README.md, pyproject.toml, src/*, app/*, tests/*).")
        elif node.phase.value == "implement_vertical_slice":
            lines.append("Implement one minimal but usable vertical slice with a real entrypoint.")
        elif node.phase.value == "validate_project":
            lines.append("Run targeted smoke/test validation and capture concrete command evidence.")
        elif node.phase.value == "summarize_outcome":
            lines.append("Summarize what was built, where files live, how to run, and validation outcomes.")
    lines.append("Avoid asking the user clarifying/confirmation questions; pick a reasonable default and continue autonomously.")
    lines.append("Stop when node objective is satisfied or clearly blocked. Provide concrete evidence.")
    return "\n".join(lines)


_CLARIFICATION_PATTERNS: tuple[str, ...] = (
    r"\bplease confirm\b",
    r"\bdo you want me to proceed\b",
    r"\bshould i continue\b",
    r"\bwould you like me to\b",
    r"\bwait for verification or continue\b",
    r"\boption\s+[a-z0-9]\b",
    r"\bconfirm\b.*\bproceed\b",
)


def _detect_clarification_request(text: str) -> bool:
    low = (text or "").lower()
    if "?" not in low:
        return False
    return any(re.search(pattern, low) for pattern in _CLARIFICATION_PATTERNS)


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


def _extract_changed_files_from_result(result: dict[str, Any], before: set[str], after: set[str]) -> list[str]:
    execution = _extract_execution_payload(result)
    from_execution = [
        str(p).strip()
        for p in list(execution.get("changed_files", execution.get("files_changed", [])) or [])
        if str(p).strip()
    ]
    if from_execution:
        return sorted(dict.fromkeys(from_execution))
    return sorted(after - before)


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


def _extract_command_results_from_execution(execution: dict[str, Any]) -> list[CommandResult]:
    out: list[CommandResult] = []
    for item in list(execution.get("command_results", []) or []):
        if not isinstance(item, dict):
            continue
        cmd = str(item.get("command", "")).strip()
        if not cmd:
            continue
        try:
            exit_code = int(item.get("exit", 1))
        except (TypeError, ValueError):
            exit_code = 1
        out.append(
            CommandResult(
                command=cmd,
                exit=exit_code,
                stdout=str(item.get("stdout", ""))[:4000],
                stderr=str(item.get("stderr", ""))[:4000],
                source="execution_payload",
                timed_out=bool(item.get("timed_out")),
            )
        )
    return out


def _extract_failures_from_execution(execution: dict[str, Any]) -> list[str]:
    return [
        str(item).strip()
        for item in list(execution.get("tool_failures", execution.get("runner_failures", [])) or [])
        if str(item).strip()
    ]


def _extract_model_activity_from_execution(execution: dict[str, Any]) -> dict[str, int]:
    provided = dict(execution.get("model_activity", {}) or {})
    return {
        "tool_invocations": int(provided.get("tool_invocations", 0) or 0),
        "tool_results": int(provided.get("tool_results", 0) or 0),
        "tool_errors": int(provided.get("tool_errors", 0) or 0),
        "responses": int(provided.get("responses", 0) or 0),
        "requests": int(provided.get("requests", 0) or 0),
    }


def _extract_structured_tool_data_from_transcript(result: dict[str, Any]) -> tuple[list[CommandResult], list[str], dict[str, int]]:
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
        if tool_name in {"bash", "powershell", "shell", "cmd"}:
            parsed = _parse_command_result_content(tr.get("content", ""))
            if parsed:
                commands.extend(parsed)
            else:
                command_text = str((inv.get("input") or {}).get("command", "")).strip()
                if command_text:
                    commands.append(CommandResult(command=command_text, exit=1 if is_error else 0, source="tool_invocation"))
    dedup: dict[tuple[str, int], CommandResult] = {}
    for record in commands:
        dedup[(record.command, record.exit)] = record
    return list(dedup.values()), failures, model_activity


def _fallback_parse_command_results_from_tool_text(result: dict[str, Any]) -> list[CommandResult]:
    transcript = result.get("transcript", {}) if isinstance(result.get("transcript"), dict) else {}
    commands: list[CommandResult] = []
    for tr in list(transcript.get("tool_results", []) or []):
        if not isinstance(tr, dict):
            continue
        commands.extend(_parse_command_result_content(tr.get("content", "")))
    dedup: dict[tuple[str, int], CommandResult] = {}
    for record in commands:
        dedup[(record.command, record.exit)] = record
    return list(dedup.values())


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
    normalized = result if isinstance(result, dict) else {}
    execution = _extract_execution_payload(normalized)
    changed = _extract_changed_files_from_result(normalized, before, after)
    execution_commands = list(execution.get("validation_artifacts", []) or [])
    command_results = _extract_command_results_from_execution(execution)
    failures = _extract_failures_from_execution(execution)
    model_activity = _extract_model_activity_from_execution(execution)
    if not command_results or not failures:
        transcript_commands, transcript_failures, transcript_activity = _extract_structured_tool_data_from_transcript(normalized)
        if not command_results:
            command_results = transcript_commands
        if not failures:
            failures = transcript_failures
        if not any(model_activity.values()):
            model_activity = transcript_activity
    if not command_results:
        command_results = _fallback_parse_command_results_from_tool_text(normalized)
    commands_run = [c.command for c in command_results]
    if not commands_run:
        commands_run = [str(x).split(" (exit=", 1)[0] for x in execution_commands if str(x).strip()]
    text_blocks = (normalized.get("response", {}) or {}).get("content", []) if isinstance(normalized, dict) else []
    text = "\n".join(str(b.get("text", "")) for b in text_blocks if isinstance(b, dict))
    clarification_requested = _detect_clarification_request(text)
    patch_detected = bool(execution.get("patch_detected", bool(changed)))
    meaningful_patch = bool(execution.get("meaningful_patch", bool(execution.get("intentional_changes")) or bool(changed)))
    prose_only = bool(execution.get("prose_only", (not changed) and (not command_results) and model_activity.get("tool_results", 0) == 0 and bool(text.strip())))
    acted = bool(execution.get("acted", patch_detected or bool(command_results) or model_activity.get("tool_invocations", 0) > 0))
    transcript_summary = (
        f"tools={model_activity.get('tool_results', 0)} "
        f"errors={model_activity.get('tool_errors', 0)} "
        f"commands={len(command_results)} changed={len(changed)} clarification={1 if clarification_requested else 0}"
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
        clarification_requested=clarification_requested,
        execution_payload={
            "changed_files": list(changed),
            "patch_detected": patch_detected,
            "meaningful_patch": meaningful_patch,
            "intentional_changes": list(execution.get("intentional_changes", []) or []),
            "incidental_changes": list(execution.get("incidental_changes", []) or []),
            "command_results": [asdict(item) for item in command_results],
            "tool_failures": list(failures),
            "validation_artifacts": list(execution.get("validation_artifacts", []) or []),
            "terminated_reason": str(execution.get("terminated_reason", "")),
            "model_activity": dict(model_activity),
            "prose_only": prose_only,
            "acted": acted,
            "clarification_requested": clarification_requested,
        },
    )
