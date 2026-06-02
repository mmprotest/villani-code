from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

CompletionConfidence = Literal["unsupported", "partial", "supported"]
EvaluatorStatus = Literal["progressing", "unverified", "stalled", "blocked", "ready_to_finish"]
NextMode = Literal[
    "continue",
    "observe_result",
    "repair_from_failure",
    "replan",
    "gather_completion_evidence",
    "finish",
]


@dataclass(slots=True)
class EvidenceRecord:
    kind: str
    source_action: str | None
    observation: str
    supports: str | None
    contradicts: str | None
    timestamp_or_turn: int


@dataclass(slots=True)
class EvidenceLoopEvaluation:
    status: EvaluatorStatus
    goal_alignment: str
    strongest_evidence: list[str] = field(default_factory=list)
    active_blocker: str | None = None
    unsupported_claims: list[str] = field(default_factory=list)
    required_next_mode: NextMode = "continue"
    completion_confidence: CompletionConfidence | None = None
    supporting_evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceLoopState:
    current_goal: str | None = None
    current_subgoal: str | None = None
    last_material_action: str | None = None
    last_material_action_intent: str | None = None
    last_observation: str | None = None
    last_observation_supports_progress: bool | None = None
    current_evidence: list[EvidenceRecord] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)
    active_blocker: str | None = None
    repeated_failure_signature: str | None = None
    consecutive_actions_without_observation: int = 0
    consecutive_failed_attempts_on_same_approach: int = 0
    completion_evidence: list[EvidenceRecord] = field(default_factory=list)
    completion_confidence: CompletionConfidence = "unsupported"
    detected_material_actions: list[dict[str, Any]] = field(default_factory=list)
    detected_observations: list[dict[str, Any]] = field(default_factory=list)
    interventions: list[dict[str, Any]] = field(default_factory=list)
    repeated_failure_detections: list[dict[str, Any]] = field(default_factory=list)
    wandering_detections: list[dict[str, Any]] = field(default_factory=list)
    evaluator_outputs: list[dict[str, Any]] = field(default_factory=list)
    material_action_count: int = 0
    observation_count: int = 0
    last_action_signature: str | None = None
    repeated_observation_signature: str | None = None
    consecutive_same_observations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_OBSERVATION_TOOLS = {"Read", "Ls", "Grep", "Glob", "Search", "GitStatus", "GitDiff", "GitLog", "GitBranch", "WebFetch"}
_MATERIAL_TOOLS = {"Write", "Patch", "GitCheckout", "GitCommit", "SubmitPlan"}
_READONLY_BASH_PREFIXES = (
    "pwd",
    "ls",
    "cat",
    "sed ",
    "awk ",
    "rg ",
    "grep ",
    "find ",
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "python -c",
    "python3 -c",
)


def classify_tool_action(tool_name: str, tool_input: dict[str, Any]) -> Literal["material", "observation"]:
    if tool_name in _MATERIAL_TOOLS:
        return "material"
    if tool_name == "Bash":
        command = str(tool_input.get("command", "")).strip().lower()
        if not command:
            return "observation"
        if command.startswith(_READONLY_BASH_PREFIXES):
            return "observation"
        return "material"
    if tool_name in _OBSERVATION_TOOLS:
        return "observation"
    return "observation"


def describe_action(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        command = str(tool_input.get("command", "")).strip()
        return f"Bash: {command}" if command else "Bash"
    for key in ("file_path", "path", "url", "pattern", "query"):
        value = tool_input.get(key)
        if value:
            return f"{tool_name}: {value}"
    return f"{tool_name}: {json.dumps(tool_input, sort_keys=True, ensure_ascii=False)[:240]}"


def action_signature(tool_name: str, tool_input: dict[str, Any]) -> str:
    text = describe_action(tool_name, tool_input).lower()
    text = re.sub(r"\b\d+\b", "#", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def summarize_observation(tool_name: str, result: dict[str, Any], limit: int = 500) -> str:
    content = str(result.get("content", ""))
    if tool_name == "Bash":
        try:
            decoded = json.loads(content)
        except Exception:
            decoded = None
        if isinstance(decoded, dict):
            command = decoded.get("command", "")
            exit_code = decoded.get("exit_code", decoded.get("exit", ""))
            stdout = str(decoded.get("stdout", "")).strip()
            stderr = str(decoded.get("stderr", "")).strip()
            parts = [f"command={command!r}", f"exit={exit_code}"]
            if stdout:
                parts.append(f"stdout={stdout[:220]!r}")
            if stderr:
                parts.append(f"stderr={stderr[:220]!r}")
            return "; ".join(parts)[:limit]
    text = content.replace("\x00", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def result_indicates_progress(result: dict[str, Any]) -> bool:
    if result.get("is_error"):
        return False
    content = str(result.get("content", ""))
    try:
        decoded = json.loads(content)
    except Exception:
        decoded = None
    if isinstance(decoded, dict) and "exit_code" in decoded:
        try:
            return int(decoded.get("exit_code")) == 0
        except Exception:
            return False
    return True


def record_tool_result(
    state: EvidenceLoopState,
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    result: dict[str, Any],
    turn_index: int,
) -> list[str]:
    kind = classify_tool_action(tool_name, tool_input)
    action = describe_action(tool_name, tool_input)
    signature = action_signature(tool_name, tool_input)
    observation = summarize_observation(tool_name, result)
    supports_progress = result_indicates_progress(result)
    redirects: list[str] = []

    if kind == "material":
        state.material_action_count += 1
        state.last_material_action = action
        state.last_material_action_intent = action
        state.last_action_signature = signature
        state.current_subgoal = state.current_subgoal or state.current_goal
        state.consecutive_actions_without_observation += 1
        event = {"turn": turn_index, "action": action, "signature": signature, "result_observation": observation, "is_error": bool(result.get("is_error"))}
        state.detected_material_actions.append(event)
        if result.get("is_error"):
            evidence = EvidenceRecord(
                kind="failed_material_action",
                source_action=action,
                observation=observation,
                supports=None,
                contradicts=state.current_subgoal or state.current_goal,
                timestamp_or_turn=turn_index,
            )
            state.current_evidence.append(evidence)
            state.last_observation = observation
            state.last_observation_supports_progress = False
            state.active_blocker = observation or "The material action failed."
            if signature == state.repeated_failure_signature:
                state.consecutive_failed_attempts_on_same_approach += 1
            else:
                state.repeated_failure_signature = signature
                state.consecutive_failed_attempts_on_same_approach = 1
            redirects.append(build_recovery_redirect(state, action, observation))
        elif tool_name == "Bash" and observation:
            evidence = EvidenceRecord(
                kind="command_result",
                source_action=action,
                observation=observation,
                supports=state.current_subgoal or state.current_goal,
                contradicts=None,
                timestamp_or_turn=turn_index,
            )
            state.current_evidence.append(evidence)
            state.completion_evidence.append(evidence)
            state.last_observation = observation
            state.last_observation_supports_progress = True
            state.consecutive_actions_without_observation = 0
        return redirects

    state.observation_count += 1
    state.last_observation = observation
    state.last_observation_supports_progress = supports_progress
    state.consecutive_actions_without_observation = 0
    evidence = EvidenceRecord(
        kind="observation" if supports_progress else "contradictory_observation",
        source_action=state.last_material_action,
        observation=observation,
        supports=state.current_subgoal or state.current_goal if supports_progress else None,
        contradicts=state.current_subgoal or state.current_goal if not supports_progress else None,
        timestamp_or_turn=turn_index,
    )
    state.current_evidence.append(evidence)
    if supports_progress:
        state.completion_evidence.append(evidence)
        state.active_blocker = None
        state.consecutive_failed_attempts_on_same_approach = 0
    else:
        state.active_blocker = observation or "The latest observation did not show progress."
    obs_event = {"turn": turn_index, "action": action, "signature": signature, "observation": observation, "supports_progress": supports_progress}
    state.detected_observations.append(obs_event)
    if signature == state.repeated_observation_signature:
        state.consecutive_same_observations += 1
    else:
        state.repeated_observation_signature = signature
        state.consecutive_same_observations = 1
    return redirects


def evaluate_checkpoint(
    state: EvidenceLoopState,
    *,
    trigger: str,
    final_text: str = "",
    turn_index: int = 0,
) -> EvidenceLoopEvaluation:
    strongest = [record.observation for record in state.current_evidence[-5:] if record.observation]
    unsupported_claims = list(state.unverified_claims[-5:])
    if state.consecutive_failed_attempts_on_same_approach >= 2:
        evaluation = EvidenceLoopEvaluation(
            status="stalled",
            goal_alignment="A substantially similar failed approach is being repeated.",
            strongest_evidence=strongest,
            active_blocker=state.active_blocker,
            unsupported_claims=unsupported_claims,
            required_next_mode="replan",
        )
    elif state.consecutive_same_observations >= 3 and state.material_action_count > 0:
        evaluation = EvidenceLoopEvaluation(
            status="stalled",
            goal_alignment="The same state is being inspected repeatedly without new evidence.",
            strongest_evidence=strongest,
            active_blocker=state.active_blocker,
            unsupported_claims=unsupported_claims,
            required_next_mode="replan",
        )
    elif state.consecutive_actions_without_observation >= 2:
        evaluation = EvidenceLoopEvaluation(
            status="unverified",
            goal_alignment="Recent material actions have not been followed by consequence-focused observation.",
            strongest_evidence=strongest,
            active_blocker=state.active_blocker,
            unsupported_claims=unsupported_claims,
            required_next_mode="observe_result",
        )
    elif trigger == "completion":
        completion = evaluate_completion(state, final_text=final_text)
        evaluation = EvidenceLoopEvaluation(
            status="ready_to_finish" if completion["completion_confidence"] == "supported" else "unverified",
            goal_alignment="Completion is assessed against observed evidence gathered in the transcript.",
            strongest_evidence=strongest,
            active_blocker=state.active_blocker,
            unsupported_claims=unsupported_claims,
            required_next_mode="finish" if completion["completion_confidence"] == "supported" else "gather_completion_evidence",
            completion_confidence=completion["completion_confidence"],
            supporting_evidence=completion["supporting_evidence"],
            missing_evidence=completion["missing_evidence"],
        )
    else:
        evaluation = EvidenceLoopEvaluation(
            status="progressing",
            goal_alignment="Recent actions are aligned with the current goal and evidence state.",
            strongest_evidence=strongest,
            active_blocker=state.active_blocker,
            unsupported_claims=unsupported_claims,
            required_next_mode="continue",
        )
    state.evaluator_outputs.append({"trigger": trigger, "turn": turn_index, **asdict(evaluation)})
    return evaluation


def evaluate_completion(state: EvidenceLoopState, *, final_text: str = "") -> dict[str, Any]:
    limitation_text = final_text.lower()
    states_limitation = any(token in limitation_text for token in ("unable to verify", "could not verify", "cannot verify", "blocked", "limitation"))
    support = [record.observation for record in state.completion_evidence[-5:] if record.observation]
    if state.material_action_count == 0:
        confidence: CompletionConfidence = "supported"
        missing: list[str] = []
    elif state.consecutive_actions_without_observation > 0:
        confidence = "unsupported"
        missing = ["Observe the consequence of the most recent material action before declaring completion."]
    elif support:
        confidence = "supported"
        missing = []
    elif states_limitation and state.current_evidence:
        confidence = "supported"
        support = [record.observation for record in state.current_evidence[-3:] if record.observation]
        missing = []
    else:
        confidence = "partial" if state.current_evidence else "unsupported"
        missing = ["Evidence in the transcript does not yet support the requested outcome."]
    state.completion_confidence = confidence
    return {"completion_confidence": confidence, "supporting_evidence": support, "missing_evidence": missing}


def maybe_build_intervention(state: EvidenceLoopState, *, turn_index: int) -> str | None:
    if (
        state.consecutive_actions_without_observation < 2
        and state.consecutive_failed_attempts_on_same_approach < 2
        and state.consecutive_same_observations < 3
    ):
        return None
    evaluation = evaluate_checkpoint(state, trigger="trajectory", turn_index=turn_index)
    if evaluation.required_next_mode == "replan":
        text = build_stall_redirect()
        state.interventions.append({"turn": turn_index, "kind": "replan", "text": text, "evaluation": asdict(evaluation)})
        state.repeated_failure_detections.append({"turn": turn_index, "signature": state.repeated_failure_signature or state.repeated_observation_signature or ""})
        return text
    if evaluation.required_next_mode == "observe_result":
        text = (
            "You have taken actions intended to advance the task without obtaining evidence that they worked.\n\n"
            "Before making further speculative changes, inspect the current result using the most informative available tool or check. "
            "Use that observation to decide the next action."
        )
        state.interventions.append({"turn": turn_index, "kind": "observe_result", "text": text, "evaluation": asdict(evaluation)})
        return text
    return None


def build_recovery_redirect(state: EvidenceLoopState, action: str, observation: str) -> str:
    evidence = "\n".join(f"- {item.observation}" for item in state.current_evidence[-3:] if item.observation) or "- No supporting evidence yet."
    return (
        "The previous approach did not produce evidence of progress.\n\n"
        f"Current subgoal:\n{state.current_subgoal or state.current_goal or 'Unspecified current goal'}\n\n"
        f"Attempted action:\n{action}\n\n"
        f"Observed result:\n{observation or 'No observable result captured.'}\n\n"
        f"Current evidence:\n{evidence}\n\n"
        "Choose the next action based on this evidence. Do not repeat the same approach unless you have identified why it should now behave differently."
    )


def build_stall_redirect() -> str:
    return (
        "Progress has stalled.\n\n"
        "Summarise:\n"
        "1. The goal currently being pursued.\n"
        "2. The strongest evidence gathered so far.\n"
        "3. The blocker preventing completion.\n"
        "4. Why the previous approach failed or remained unverified.\n"
        "5. A materially different next action that can reduce uncertainty or advance the goal.\n\n"
        "Then execute that next action."
    )


def completion_redirect(evaluation: EvidenceLoopEvaluation) -> str:
    missing = "\n".join(f"- {item}" for item in evaluation.missing_evidence) or "- Completion is not yet supported by observed evidence."
    evidence = "\n".join(f"- {item}" for item in evaluation.supporting_evidence) or "- No supporting completion evidence recorded."
    if evaluation.completion_confidence == "partial":
        return (
            "Completion is only partially supported by observed evidence.\n\n"
            f"Supporting evidence:\n{evidence}\n\n"
            f"Missing evidence or limitation to address:\n{missing}\n\n"
            "Decide how to obtain the missing evidence, or clearly state why verification is impossible with the available tools and what evidence you do have."
        )
    return (
        "Completion is not supported by observed evidence in the transcript.\n\n"
        f"Missing evidence:\n{missing}\n\n"
        "Before finishing, perform an observation or verification step using the most informative available tool or clearly explain why verification is impossible."
    )
