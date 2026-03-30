from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from villani_code.autonomy import contract_discourages_editing, contract_requires_validation, get_phase_contract, validate_phase_action
from villani_code.evidence import parse_command_evidence
from villani_code.mission import Mission, MissionExecutionState, MissionNode, ProposedAction
from villani_code.path_authority import split_internal_paths


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
    internal_changed_files: list[str] = field(default_factory=list)
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
    scratchpad = execution_state.scratchpad
    if mission.mission_type.value == "greenfield_build":
        direction = scratchpad.chosen_project_direction or str(execution_state.greenfield_selection.get("project_type", ""))
        if direction:
            lines.append("Authoritative selected objective direction: " + direction)
        if scratchpad.current_phase:
            lines.append("Authoritative mission phase: " + scratchpad.current_phase)
        if scratchpad.next_required_action:
            lines.append("Authoritative next action: " + scratchpad.next_required_action)
        if scratchpad.confirmed_deliverables:
            lines.append("Confirmed deliverables: " + ", ".join(scratchpad.confirmed_deliverables[:12]))
        if scratchpad.ignored_internal_paths:
            lines.append("Ignored internal paths: " + ", ".join(scratchpad.ignored_internal_paths[:8]))
        if scratchpad.minimal_vertical_slice_target:
            lines.append("Minimal vertical slice target: " + scratchpad.minimal_vertical_slice_target)
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
        lines.append("Files under .villani/ and .villani_code/ are internal artifacts only and do not count as project deliverables.")
        lines.append("Operate autonomously; ask user only for true hard ambiguity or destructive-risk policy conflicts.")
        if node.phase.value == "inspect_workspace":
            lines.append("Inspect workspace for constraints, sample data, README/notes hints, and feasible local project directions.")
            lines.append("Frame candidate files/concepts/validation as next-phase plans only; do not imply scaffold or implementation has already started.")
            lines.append("WRITE POLICY: READ-ONLY PHASE. Do not narrate writes and do not invoke write/patch/mkdir tools.")
        elif node.phase.value == "define_objective":
            lines.append("Produce 2-4 plausible runnable utility candidates, then choose one deterministic direction with rationale.")
            lines.append("Docs-only, README-only, and suggestion-only directions are invalid unless the user explicitly asked for docs only.")
            lines.append("WRITE POLICY: READ-ONLY PHASE. Do not narrate writes and do not invoke write/patch/mkdir tools.")
        elif node.phase.value == "scaffold_project":
            lines.append("Scaffold only the chosen project structure in user-facing paths. Avoid .villani/ outputs.")
            lines.append("This phase must create at least one real user-space file (for example README.md, pyproject.toml, src/*, app/*, tests/*).")
            lines.append("WRITE POLICY: WRITE-ALLOWED PHASE for scaffolding only.")
        elif node.phase.value == "implement_increment":
            lines.append("Implement one minimal but usable vertical slice with a real entrypoint.")
            lines.append("WRITE POLICY: WRITE-ALLOWED PHASE for targeted implementation.")
        elif node.phase.value == "validate_project":
            lines.append("Run targeted smoke/test validation and capture concrete command evidence.")
            lines.append("REQUIREMENT: Run at least one real shell command and report its exact exit status.")
            lines.append("WRITE POLICY: validation-focused phase; success requires real command evidence.")
        elif node.phase.value == "summarize_outcome":
            lines.append("Summarize what was built, where files live, how to run, and validation outcomes.")
            lines.append("WRITE POLICY: READ-ONLY PHASE. No file edits.")
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

_AUTONOMY_CONFIRMATION_REWRITES: tuple[tuple[str, str], ...] = (
    (r"\bwould you like me to\b", "Proceeding to"),
    (r"\bdo you want me to\b", "Proceeding to"),
    (r"\bshould i\b", "I will"),
    (r"\bplease confirm\b", "Confirmed by autonomous policy"),
)


def _detect_clarification_request(text: str) -> bool:
    low = (text or "").lower()
    if "?" not in low:
        return False
    return any(re.search(pattern, low) for pattern in _CLARIFICATION_PATTERNS)


def _sanitize_autonomous_text_output(text: str) -> tuple[str, bool]:
    raw = str(text or "")
    if not raw.strip():
        return raw, False
    low = raw.lower()
    needs_sanitize = any(re.search(pattern, low) for pattern in _CLARIFICATION_PATTERNS)
    if not needs_sanitize:
        return raw, False
    cleaned = raw
    for pattern, replacement in _AUTONOMY_CONFIRMATION_REWRITES:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("?", ".")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, True


def _sanitize_autonomous_response_payload(normalized: dict[str, Any], mission_type: str) -> dict[str, Any]:
    if mission_type != "greenfield_build":
        return normalized
    response = normalized.get("response") if isinstance(normalized.get("response"), dict) else {}
    blocks = response.get("content") if isinstance(response.get("content"), list) else []
    sanitized_any = False
    for block in blocks:
        if not isinstance(block, dict) or str(block.get("type", "")).lower() != "text":
            continue
        updated, changed = _sanitize_autonomous_text_output(str(block.get("text", "")))
        if changed:
            block["text"] = updated
            sanitized_any = True
    if sanitized_any:
        normalized["response"] = response
    return normalized


def _git_changed_files(repo: Path) -> list[str]:
    import subprocess

    proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for raw in proc.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip()
        if path:
            paths.append(path)
    return sorted(dict.fromkeys(paths))


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


def _extract_write_paths_from_transcript(result: dict[str, Any]) -> list[str]:
    transcript = result.get("transcript", {}) if isinstance(result.get("transcript"), dict) else {}
    tool_invocations = list(transcript.get("tool_invocations", []) or [])
    write_like_tools = {"write", "patch", "apply_patch"}
    path_keys = ("file_path", "path", "target_file", "filename")
    touched: list[str] = []
    for invocation in tool_invocations:
        if not isinstance(invocation, dict):
            continue
        tool_name = str(invocation.get("name", "")).strip().lower()
        if tool_name not in write_like_tools:
            continue
        payload = invocation.get("input")
        payload = payload if isinstance(payload, dict) else {}
        for key in path_keys:
            value = str(payload.get(key, "")).strip()
            if value:
                touched.append(value)
    return sorted(dict.fromkeys(touched))


def _extract_shell_commands_from_transcript(result: dict[str, Any]) -> list[str]:
    transcript = result.get("transcript", {}) if isinstance(result.get("transcript"), dict) else {}
    tool_invocations = list(transcript.get("tool_invocations", []) or [])
    shell_tools = {"bash", "powershell", "shell", "cmd"}
    commands: list[str] = []
    for invocation in tool_invocations:
        if not isinstance(invocation, dict):
            continue
        tool_name = str(invocation.get("name", "")).strip().lower()
        if tool_name not in shell_tools:
            continue
        payload = invocation.get("input")
        payload = payload if isinstance(payload, dict) else {}
        command = str(payload.get("command", "")).strip()
        if command:
            commands.append(command)
    return sorted(dict.fromkeys(commands))


def _extract_blocked_write_paths_from_transcript(result: dict[str, Any]) -> list[str]:
    transcript = result.get("transcript", {}) if isinstance(result.get("transcript"), dict) else {}
    tool_results = list(transcript.get("tool_results", []) or [])
    tool_invocations = list(transcript.get("tool_invocations", []) or [])
    blocked: list[str] = []
    for idx, tool_result in enumerate(tool_results):
        if not isinstance(tool_result, dict) or not bool(tool_result.get("is_error")):
            continue
        invocation = tool_invocations[idx] if idx < len(tool_invocations) and isinstance(tool_invocations[idx], dict) else {}
        tool_name = str(invocation.get("name", "")).strip().lower()
        if tool_name not in {"write", "patch", "apply_patch"}:
            continue
        content = str(tool_result.get("content", "")).lower()
        if not any(token in content for token in ("scope", "constrain", "forbidden", "not allowed", "denied")):
            continue
        payload = invocation.get("input")
        payload = payload if isinstance(payload, dict) else {}
        for key in ("file_path", "path", "target_file", "filename"):
            value = str(payload.get(key, "")).strip()
            if value:
                blocked.append(value)
    return sorted(dict.fromkeys(blocked))


_SELF_REPORTED_VALIDATION_PATTERNS: tuple[str, ...] = (
    r"\btests?\s+passed\b",
    r"\bverification\s+complete\b",
    r"\bsmoke\s+tests?\s+passed\b",
    r"\b\d+\s*/\s*\d+\s+passed\b",
)


def _detect_self_reported_validation_claim(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(pattern, low) for pattern in _SELF_REPORTED_VALIDATION_PATTERNS)


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




def _map_tool_to_action_type(tool_name: str) -> str:
    tool = str(tool_name or "").strip().lower()
    if tool in {"read", "cat"}:
        return "read_file"
    if tool in {"write"}:
        return "write_file"
    if tool in {"patch", "apply_patch"}:
        return "patch_file"
    if tool in {"list", "glob", "ls"}:
        return "list_files"
    if tool in {"mkdir", "makedirs"}:
        return "mkdir"
    if tool in {"bash", "powershell", "shell", "cmd"}:
        return "run_shell"
    return "inspect_metadata"


def _extract_proposed_actions(result: dict[str, Any], phase: str) -> list[ProposedAction]:
    transcript = result.get("transcript", {}) if isinstance(result.get("transcript"), dict) else {}
    out: list[ProposedAction] = []
    for invocation in list(transcript.get("tool_invocations", []) or []):
        if not isinstance(invocation, dict):
            continue
        action_type = _map_tool_to_action_type(str(invocation.get("name", "")))
        payload = invocation.get("input") if isinstance(invocation.get("input"), dict) else {}
        targets: list[str] = []
        for key in ("file_path", "path", "target_file", "filename"):
            value = str(payload.get(key, "")).strip()
            if value:
                targets.append(value)
        out.append(ProposedAction(phase=phase, action_type=action_type, target_paths=sorted(set(targets))))
    return out


def _validate_actions_for_phase(phase: str, actions: list[ProposedAction]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for action in actions:
        ok, reason = validate_phase_action(phase, action.action_type)
        payload = {
            "phase": action.phase,
            "action_type": action.action_type,
            "target_paths": list(action.target_paths),
        }
        if ok:
            approved.append(payload)
        else:
            payload["rejection_reason"] = reason
            rejected.append(payload)
    return approved, rejected

def _greenfield_controller_read_only_ready(phase: str, repo_signals: dict[str, Any]) -> bool:
    if phase == "define_objective":
        return True
    return bool(
        repo_signals.get("workspace_empty_or_internal_only")
        or repo_signals.get("workspace_lightweight_hints_only")
        or repo_signals.get("workspace_sparse_greenfield_like")
        or not repo_signals.get("existing_project_detected", False)
    )

def _synthesize_greenfield_controller_result(
    mission: Mission,
    node: MissionNode,
    execution_state: MissionExecutionState,
) -> MissionNodeResult | None:
    if mission.mission_type.value != "greenfield_build":
        return None
    phase = node.phase.value
    if phase not in {"inspect_workspace", "define_objective"}:
        return None

    repo_signals = dict(mission.mission_context.get("repo_signals", {}) or {})
    objective = mission.objective
    scratchpad = execution_state.scratchpad
    default_validation = list(objective.initial_validation_strategy or repo_signals.get("likely_validation_commands", []) or [])
    language_hints = list(repo_signals.get("language_hints", []) or [])
    direction = str(
        objective.direction
        or scratchpad.chosen_project_direction
        or execution_state.greenfield_selection.get("project_type", "")
    ).strip()
    approved_actions = [{"phase": phase, "action_type": "inspect_metadata", "target_paths": []}]

    if phase == "inspect_workspace":
        findings: list[str] = []
        if repo_signals.get("workspace_empty_or_internal_only"):
            findings.append("workspace empty or internal-only")
        if repo_signals.get("workspace_lightweight_hints_only"):
            findings.append("workspace has lightweight hints only")
        if not repo_signals.get("existing_project_detected", False):
            findings.append("no existing project detected")
        if repo_signals.get("workspace_sparse_greenfield_like"):
            findings.append("workspace appears sparse/partial scaffold and still greenfield-like")
        findings.append("greenfield context confirmed")
        if language_hints:
            findings.append("language/runtime hints: " + ", ".join(language_hints[:4]))
        else:
            findings.append("language/runtime hints: python (default)")
        execution_payload = {
            "changed_files": [],
            "all_changed_files": [],
            "internal_changed_files": [],
            "patch_detected": False,
            "meaningful_patch": False,
            "intentional_changes": [],
            "incidental_changes": [],
            "command_results": [],
            "inferred_command_results": [],
            "tool_failures": [],
            "validation_artifacts": [],
            "terminated_reason": "controller_native_greenfield_inspect",
            "model_activity": {"tool_invocations": 0, "tool_results": 0, "tool_errors": 0, "responses": 0, "requests": 0},
            "prose_only": False,
            "acted": True,
            "clarification_requested": False,
            "self_reported_validation_claim": False,
            "self_reported_validation_without_evidence": False,
            "attempted_write_paths": [],
            "blocked_write_paths": [],
            "shell_invocations": [],
            "phase_contract": {
                "phase": phase,
                "allowed_actions": sorted(get_phase_contract(phase).allowed_actions),
                "forbidden_actions": sorted(get_phase_contract(phase).forbidden_actions),
            },
            "approved_actions": approved_actions,
            "rejected_actions": [],
            "controller_findings": findings,
            "controller_native": True,
        }
        return MissionNodeResult(
            response={},
            changed_files=[],
            internal_changed_files=[],
            commands_run=[],
            command_results=[],
            tool_failures=[],
            patch_detected=False,
            meaningful_patch=False,
            transcript_summary="controller_native inspect findings synthesized",
            model_activity=execution_payload["model_activity"],
            prose_only=False,
            acted=True,
            clarification_requested=False,
            execution_payload=execution_payload,
        )

    fallback_repo_state = "unknown"
    if repo_signals.get("workspace_empty_or_internal_only"):
        fallback_repo_state = "empty_sandbox"
    elif repo_signals.get("workspace_lightweight_hints_only"):
        fallback_repo_state = "lightweight_hints"
    elif repo_signals.get("workspace_sparse_greenfield_like"):
        fallback_repo_state = "sparse_scaffold"
    objective_payload = {
        "repo_state_type": str(objective.repo_state_type or fallback_repo_state),
        "task_shape": str(objective.task_shape or "greenfield_build"),
        "deliverable_kind": list(objective.deliverable_kind or ["unknown"]),
        "direction": direction or "python_cli_utility",
        "initial_validation_strategy": list(default_validation[:4]) or ["python -m py_compile <entrypoint>"],
    }
    execution_payload = {
        "changed_files": [],
        "all_changed_files": [],
        "internal_changed_files": [],
        "patch_detected": False,
        "meaningful_patch": False,
        "intentional_changes": [],
        "incidental_changes": [],
        "command_results": [],
        "inferred_command_results": [],
        "tool_failures": [],
        "validation_artifacts": [],
        "terminated_reason": "controller_native_greenfield_objective",
        "model_activity": {"tool_invocations": 0, "tool_results": 0, "tool_errors": 0, "responses": 0, "requests": 0},
        "prose_only": False,
        "acted": True,
        "clarification_requested": False,
        "self_reported_validation_claim": False,
        "self_reported_validation_without_evidence": False,
        "attempted_write_paths": [],
        "blocked_write_paths": [],
        "shell_invocations": [],
        "phase_contract": {
            "phase": phase,
            "allowed_actions": sorted(get_phase_contract(phase).allowed_actions),
            "forbidden_actions": sorted(get_phase_contract(phase).forbidden_actions),
        },
        "approved_actions": approved_actions,
        "rejected_actions": [],
        "controller_objective": objective_payload,
        "controller_native": True,
    }
    return MissionNodeResult(
        response={},
        changed_files=[],
        internal_changed_files=[],
        commands_run=[],
        command_results=[],
        tool_failures=[],
        patch_detected=False,
        meaningful_patch=False,
        transcript_summary="controller_native objective synthesized",
        model_activity=execution_payload["model_activity"],
        prose_only=False,
        acted=True,
        clarification_requested=False,
        execution_payload=execution_payload,
    )


def execute_mission_node_with_runner(
    runner: Any,
    mission: Mission,
    node: MissionNode,
    execution_state: MissionExecutionState,
) -> MissionNodeResult:
    controller_result = _synthesize_greenfield_controller_result(mission, node, execution_state)
    repo_signals = dict(mission.mission_context.get("repo_signals", {}) or {})
    if controller_result is not None and _greenfield_controller_read_only_ready(node.phase.value, repo_signals):
        return controller_result

    instruction = build_node_instruction(mission, node, execution_state)
    phase_contract = get_phase_contract(node.phase.value)
    before = set(_git_changed_files(Path(mission.repo_root)))
    prior_phase_policy = getattr(runner, "_villani_phase_tool_policy", None)
    if mission.mission_type.value == "greenfield_build":
        runner._villani_phase_tool_policy = {
            "mission_type": "greenfield_build",
            "node_phase": node.phase.value,
            "read_only_phase": node.phase.value in {"inspect_workspace", "define_objective", "summarize_outcome"},
            "allow_shell_commands": node.phase.value in {"validate_project"},
            "allow_mutating_tools": node.phase.value in {"scaffold_project", "implement_increment"},
            "allow_validation_shell": node.phase.value == "validate_project",
        }
    try:
        result = runner.run(instruction)
    finally:
        runner._villani_phase_tool_policy = prior_phase_policy
    after = set(_git_changed_files(Path(mission.repo_root)))
    normalized = result if isinstance(result, dict) else {}
    normalized = _sanitize_autonomous_response_payload(normalized, mission.mission_type.value)
    proposed_actions = _extract_proposed_actions(normalized, node.phase.value)
    approved_actions, rejected_actions = _validate_actions_for_phase(node.phase.value, proposed_actions)
    execution = _extract_execution_payload(normalized)
    changed_all = _extract_changed_files_from_result(normalized, before, after)
    write_paths = _extract_write_paths_from_transcript(normalized)
    shell_invocations = _extract_shell_commands_from_transcript(normalized)
    changed, internal_changed = split_internal_paths(changed_all)
    blocked_write_paths = sorted(
        dict.fromkeys(
            [path for path in write_paths if path not in set(changed_all)]
            + _extract_blocked_write_paths_from_transcript(normalized)
        )
    )
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
    inferred_command_results: list[CommandResult] = []
    if not command_results:
        inferred_command_results = _fallback_parse_command_results_from_tool_text(normalized)
    commands_run = [c.command for c in command_results] or [c.command for c in inferred_command_results]
    if not commands_run:
        commands_run = [str(x).split(" (exit=", 1)[0] for x in execution_commands if str(x).strip()]
    text_blocks = (normalized.get("response", {}) or {}).get("content", []) if isinstance(normalized, dict) else []
    text = "\n".join(str(b.get("text", "")) for b in text_blocks if isinstance(b, dict))
    clarification_requested = _detect_clarification_request(text)
    self_reported_validation_claim = _detect_self_reported_validation_claim(text)
    patch_detected = bool(execution.get("patch_detected", bool(changed)))
    meaningful_patch = bool(execution.get("meaningful_patch", bool(execution.get("intentional_changes")) or bool(changed)))
    prose_only = bool(execution.get("prose_only", (not changed) and (not command_results) and model_activity.get("tool_results", 0) == 0 and bool(text.strip())))
    acted = bool(execution.get("acted", patch_detected or bool(command_results) or model_activity.get("tool_invocations", 0) > 0))
    if controller_result is not None:
        merged_actions = approved_actions + list(controller_result.execution_payload.get("approved_actions", []) or [])
        approved_actions = []
        seen_actions: set[str] = set()
        for action in merged_actions:
            if not isinstance(action, dict):
                continue
            action_key = json.dumps(action, sort_keys=True)
            if action_key in seen_actions:
                continue
            seen_actions.add(action_key)
            approved_actions.append(action)
        static_cast = list(controller_result.execution_payload.get("controller_findings", []) or [])
        if static_cast:
            execution.setdefault("controller_findings", list(static_cast))
        controller_objective = dict(controller_result.execution_payload.get("controller_objective", {}) or {})
        if controller_objective:
            execution.setdefault("controller_objective", controller_objective)
        prose_only = False
        acted = True
        clarification_requested = False
    transcript_summary = (
        f"tools={model_activity.get('tool_results', 0)} "
        f"errors={model_activity.get('tool_errors', 0)} "
        f"commands={len(command_results)} changed={len(changed)} internal_changed={len(internal_changed)} clarification={1 if clarification_requested else 0}"
    )
    return MissionNodeResult(
        response=normalized,
        changed_files=changed,
        internal_changed_files=internal_changed,
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
            "all_changed_files": list(changed_all),
            "internal_changed_files": list(internal_changed),
            "patch_detected": patch_detected,
            "meaningful_patch": meaningful_patch,
            "intentional_changes": list(execution.get("intentional_changes", []) or []),
            "incidental_changes": list(execution.get("incidental_changes", []) or []),
            "command_results": [asdict(item) for item in command_results],
            "inferred_command_results": [asdict(item) for item in inferred_command_results],
            "tool_failures": list(failures),
            "validation_artifacts": list(execution.get("validation_artifacts", []) or []),
            "terminated_reason": str(execution.get("terminated_reason", "")),
            "model_activity": dict(model_activity),
            "prose_only": prose_only,
            "acted": acted,
            "clarification_requested": clarification_requested,
            "self_reported_validation_claim": self_reported_validation_claim,
            "self_reported_validation_without_evidence": bool(
                self_reported_validation_claim and not command_results
            ),
            "attempted_write_paths": list(write_paths),
            "blocked_write_paths": list(blocked_write_paths),
            "shell_invocations": list(shell_invocations),
            "phase_contract": {
                "phase": phase_contract.phase,
                "allowed_actions": sorted(phase_contract.allowed_actions),
                "forbidden_actions": sorted(phase_contract.forbidden_actions),
            },
            "approved_actions": approved_actions,
            "rejected_actions": rejected_actions,
            "controller_native": bool(controller_result is not None),
            "controller_findings": list(execution.get("controller_findings", []) or []),
            "controller_objective": dict(execution.get("controller_objective", {}) or {}),
        },
    )
