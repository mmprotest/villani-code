from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EvaluatorStatus = Literal["progressing", "unverified", "stalled", "blocked", "ready_to_finish"]
NextMode = Literal[
    "continue",
    "observe_result",
    "repair_from_failure",
    "replan",
    "gather_completion_evidence",
    "finish",
]
CompletionStatus = Literal["not_requested", "unsupported", "partial", "supported"]

_VALID_STATUSES = {"progressing", "unverified", "stalled", "blocked", "ready_to_finish"}
_VALID_NEXT_MODES = {"continue", "observe_result", "repair_from_failure", "replan", "gather_completion_evidence", "finish"}


@dataclass(slots=True)
class ActionRecord:
    turn_id: int
    tool_name: str | None
    action_summary: str
    action_result_summary: str | None = None
    succeeded_operationally: bool | None = None
    error_summary: str | None = None
    action_id: str | None = None


@dataclass(slots=True)
class ObservationRecord:
    turn_id: int
    source: str
    observation_summary: str
    raw_excerpt: str | None = None
    operational_error: str | None = None
    action_id: str | None = None


@dataclass(slots=True)
class EvidenceEvaluation:
    status: EvaluatorStatus
    goal_alignment_summary: str
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    active_blocker: str | None = None
    unsupported_claims: list[str] = field(default_factory=list)
    required_next_mode: NextMode = "continue"
    reason: str = ""


@dataclass(slots=True)
class InterventionRecord:
    turn_id: int
    kind: str
    message: str
    evaluation: dict[str, Any] | None = None


@dataclass(slots=True)
class EvidenceLoopState:
    current_goal: str | None = None
    current_subgoal: str | None = None
    recent_actions: list[ActionRecord] = field(default_factory=list)
    recent_observations: list[ObservationRecord] = field(default_factory=list)
    active_blocker: str | None = None
    unresolved_uncertainties: list[str] = field(default_factory=list)
    consecutive_material_actions_without_evaluation: int = 0
    repeated_failure_or_stall_count: int = 0
    last_evaluation: EvidenceEvaluation | None = None
    completion_status: CompletionStatus = "not_requested"
    completion_supporting_evidence: list[str] = field(default_factory=list)
    completion_missing_evidence: list[str] = field(default_factory=list)
    interventions: list[InterventionRecord] = field(default_factory=list)
    evaluator_invocations: list[dict[str, Any]] = field(default_factory=list)
    evaluator_outputs: list[dict[str, Any]] = field(default_factory=list)
    evaluator_failures: list[dict[str, Any]] = field(default_factory=list)
    completion_attempts: list[dict[str, Any]] = field(default_factory=list)
    raw_action_count: int = 0
    raw_observation_count: int = 0
    repeated_action_signature: str | None = None
    repeated_action_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Neutral operational metadata only. This is not goal-relevance or validation logic.
_MATERIAL_TOOL_NAMES = {"Write", "Patch", "GitCheckout", "GitCommit", "SubmitPlan", "Bash", "WebFetch"}


def is_material_tool_event(tool_name: str) -> bool:
    return tool_name in _MATERIAL_TOOL_NAMES


def summarize_action(tool_name: str, tool_input: dict[str, Any]) -> str:
    safe = {k: v for k, v in tool_input.items() if k not in {"content", "unified_diff"}}
    if "content" in tool_input:
        safe["content_chars"] = len(str(tool_input.get("content", "")))
    if "unified_diff" in tool_input:
        safe["unified_diff_chars"] = len(str(tool_input.get("unified_diff", "")))
    return f"{tool_name}: {json.dumps(safe, sort_keys=True, ensure_ascii=False)[:500]}"


def summarize_result(result: dict[str, Any], limit: int = 700) -> str:
    content = str(result.get("content", ""))
    text = content.replace("\x00", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def action_signature(tool_name: str, tool_input: dict[str, Any]) -> str:
    summary = summarize_action(tool_name, tool_input).lower()
    summary = re.sub(r"\b[0-9a-f]{6,}\b", "#", summary)
    summary = re.sub(r"\b\d+\b", "#", summary)
    return re.sub(r"\s+", " ", summary).strip()[:400]


def record_tool_result(
    state: EvidenceLoopState,
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    result: dict[str, Any],
    turn_index: int,
    tool_use_id: str | None = None,
) -> None:
    """Record raw tool activity without treating it as goal-supporting evidence."""

    action_summary = summarize_action(tool_name, tool_input)
    result_summary = summarize_result(result)
    error_summary = result_summary if bool(result.get("is_error")) else None
    action = ActionRecord(
        turn_id=turn_index,
        tool_name=tool_name,
        action_summary=action_summary,
        action_result_summary=result_summary,
        succeeded_operationally=not bool(result.get("is_error")),
        error_summary=error_summary,
        action_id=tool_use_id,
    )
    state.recent_actions.append(action)
    state.raw_action_count += 1

    observation = ObservationRecord(
        turn_id=turn_index,
        source=tool_name,
        observation_summary=result_summary,
        raw_excerpt=result_summary,
        operational_error=error_summary,
        action_id=tool_use_id,
    )
    state.recent_observations.append(observation)
    state.raw_observation_count += 1

    if is_material_tool_event(tool_name):
        state.consecutive_material_actions_without_evaluation += 1
    signature = action_signature(tool_name, tool_input)
    if signature == state.repeated_action_signature:
        state.repeated_action_count += 1
    else:
        state.repeated_action_signature = signature
        state.repeated_action_count = 1
    if result.get("is_error"):
        state.active_blocker = error_summary or "An operational tool error occurred."
        state.unresolved_uncertainties.append(state.active_blocker)
        if state.repeated_action_count >= 2:
            state.repeated_failure_or_stall_count += 1




def record_observation(
    state: EvidenceLoopState,
    *,
    source: str,
    observation_summary: str,
    turn_index: int,
    operational_error: str | None = None,
    raw_excerpt: str | None = None,
) -> None:
    """Record a neutral observation from a side check or direct inspection."""

    summary = summarize_result({"content": observation_summary, "is_error": bool(operational_error)})
    observation = ObservationRecord(
        turn_id=turn_index,
        source=source,
        observation_summary=summary,
        raw_excerpt=raw_excerpt or summary,
        operational_error=operational_error,
        action_id=None,
    )
    state.recent_observations.append(observation)
    state.raw_observation_count += 1
    if operational_error:
        state.active_blocker = operational_error
        state.unresolved_uncertainties.append(operational_error)

def parse_evaluation_payload(raw_text: str) -> EvidenceEvaluation:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("evaluator response did not contain a JSON object")
        text = match.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("evaluator response was not a JSON object")
    status = str(data.get("status", "")).strip()
    next_mode = str(data.get("required_next_mode", "")).strip()
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid evaluator status: {status!r}")
    if next_mode not in _VALID_NEXT_MODES:
        raise ValueError(f"invalid evaluator required_next_mode: {next_mode!r}")

    def str_list(key: str) -> list[str]:
        value = data.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        return [str(item).strip() for item in value if str(item).strip()]

    return EvidenceEvaluation(
        status=status,  # type: ignore[arg-type]
        goal_alignment_summary=str(data.get("goal_alignment_summary", data.get("goal_alignment", ""))).strip(),
        supporting_evidence=str_list("supporting_evidence"),
        contradicting_evidence=str_list("contradicting_evidence"),
        missing_evidence=str_list("missing_evidence"),
        active_blocker=str(data["active_blocker"]).strip() if data.get("active_blocker") is not None else None,
        unsupported_claims=str_list("unsupported_claims"),
        required_next_mode=next_mode,  # type: ignore[arg-type]
        reason=str(data.get("reason", "")).strip(),
    )


def build_evaluator_prompt(
    state: EvidenceLoopState,
    *,
    trigger: str,
    attempted_completion: str = "",
    max_records: int = 8,
) -> str:
    recent_actions = [asdict(item) for item in state.recent_actions[-max_records:]]
    recent_observations = [asdict(item) for item in state.recent_observations[-max_records:]]
    previous = asdict(state.last_evaluation) if state.last_evaluation is not None else None
    payload = {
        "trigger": trigger,
        "original_task": state.current_goal,
        "current_subgoal": state.current_subgoal,
        "recent_actions_neutral": recent_actions,
        "recent_observations_neutral": recent_observations,
        "existing_semantic_supporting_evidence": state.completion_supporting_evidence,
        "unresolved_uncertainties": state.unresolved_uncertainties[-5:],
        "active_blocker": state.active_blocker,
        "attempted_completion_message": attempted_completion,
        "previous_evaluation": previous,
    }
    return (
        "You are Villani's bounded semantic evidence evaluator. Assess whether the neutral events support progress "
        "toward the original user task. Do not propose concrete commands or task-specific validation methods. "
        "Operational success only means a tool ran; it is not automatically evidence that the user's goal was met. "
        "Reads, command outputs, external responses and checks may be irrelevant. Inconclusive evidence must not be "
        "treated as positive support. For completion, return ready_to_finish/finish only when the available observations "
        "semantically support the final claim or a clearly stated limitation.\n\n"
        "Return exactly one JSON object with keys: status, goal_alignment_summary, supporting_evidence, "
        "contradicting_evidence, missing_evidence, active_blocker, unsupported_claims, required_next_mode, reason.\n"
        "Allowed status values: progressing, unverified, stalled, blocked, ready_to_finish.\n"
        "Allowed required_next_mode values: continue, observe_result, repair_from_failure, replan, "
        "gather_completion_evidence, finish.\n\n"
        f"Evaluation context JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def conservative_evaluation(trigger: str, reason: str) -> EvidenceEvaluation:
    if trigger == "completion":
        return EvidenceEvaluation(
            status="unverified",
            goal_alignment_summary="Semantic evidence evaluation was unavailable; completion cannot be supported conservatively.",
            missing_evidence=["A valid semantic evidence evaluation supporting completion is required."],
            unsupported_claims=["Completion attempted without a valid evaluator approval."],
            required_next_mode="gather_completion_evidence",
            reason=reason,
        )
    return EvidenceEvaluation(
        status="unverified",
        goal_alignment_summary="Semantic evidence evaluation was unavailable; progress remains unverified.",
        missing_evidence=["Obtain clear observed evidence or state the verification limitation."],
        required_next_mode="observe_result",
        reason=reason,
    )


def invoke_semantic_evaluator(
    client: Any,
    model: str,
    state: EvidenceLoopState,
    *,
    trigger: str,
    attempted_completion: str = "",
    max_tokens: int = 900,
) -> EvidenceEvaluation:
    prompt = build_evaluator_prompt(state, trigger=trigger, attempted_completion=attempted_completion)
    invocation = {
        "trigger": trigger,
        "attempted_completion": attempted_completion[:1000],
        "action_count": len(state.recent_actions),
        "observation_count": len(state.recent_observations),
    }
    state.evaluator_invocations.append(invocation)
    try:
        raw = client.create_message(
            {
                "model": model,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                "system": "",
                "max_tokens": max_tokens,
                "stream": False,
            },
            stream=False,
        )
        text = "\n".join(
            str(block.get("text", ""))
            for block in raw.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        evaluation = parse_evaluation_payload(text)
        state.last_evaluation = evaluation
        state.evaluator_outputs.append({"trigger": trigger, "evaluation": asdict(evaluation), "raw_text": text[:2000]})
        apply_evaluation(state, evaluation, trigger=trigger)
        return evaluation
    except Exception as exc:
        failure = {"trigger": trigger, "error": str(exc)}
        state.evaluator_failures.append(failure)
        evaluation = conservative_evaluation(trigger, str(exc))
        state.last_evaluation = evaluation
        state.evaluator_outputs.append({"trigger": trigger, "evaluation": asdict(evaluation), "fallback": True})
        apply_evaluation(state, evaluation, trigger=trigger)
        return evaluation


def apply_evaluation(state: EvidenceLoopState, evaluation: EvidenceEvaluation, *, trigger: str) -> None:
    state.active_blocker = evaluation.active_blocker
    if evaluation.missing_evidence:
        state.unresolved_uncertainties.extend(evaluation.missing_evidence)
    if trigger != "completion" and evaluation.required_next_mode in {"continue", "finish"}:
        state.consecutive_material_actions_without_evaluation = 0
    if trigger == "completion":
        if evaluation.status == "ready_to_finish" and evaluation.required_next_mode == "finish" and evaluation.supporting_evidence:
            state.completion_status = "supported"
            state.completion_supporting_evidence = list(evaluation.supporting_evidence)
            state.completion_missing_evidence = []
        elif evaluation.status in {"progressing", "unverified"}:
            state.completion_status = "partial" if evaluation.supporting_evidence else "unsupported"
            state.completion_supporting_evidence = list(evaluation.supporting_evidence)
            state.completion_missing_evidence = list(evaluation.missing_evidence)
        else:
            state.completion_status = "unsupported"
            state.completion_supporting_evidence = list(evaluation.supporting_evidence)
            state.completion_missing_evidence = list(evaluation.missing_evidence)


def completion_is_allowed(evaluation: EvidenceEvaluation) -> bool:
    return evaluation.status == "ready_to_finish" and evaluation.required_next_mode == "finish" and bool(evaluation.supporting_evidence)


def build_intervention_message(evaluation: EvidenceEvaluation, *, trigger: str, state: EvidenceLoopState) -> str:
    blocker = evaluation.active_blocker or state.active_blocker or "No single blocker has been confirmed yet."
    missing = "\n".join(f"- {item}" for item in evaluation.missing_evidence) or "- Clear evidence supporting the current claim or approach."
    issue = "\n".join(f"- {item}" for item in (evaluation.contradicting_evidence or evaluation.unsupported_claims)) or f"- {blocker}"
    if trigger == "completion":
        return (
            "Completion is not yet supported by the semantic evidence evaluation.\n\n"
            f"Missing evidence or limitation to address:\n{missing}\n\n"
            "Before finishing, obtain relevant observable evidence using the most informative available method for this task, "
            "or clearly state why verification is unavailable and narrow the final claim to what the evidence supports."
        )
    if evaluation.required_next_mode == "repair_from_failure":
        return (
            "The previous approach produced evidence of a problem.\n\n"
            f"Current goal or subgoal:\n{state.current_subgoal or state.current_goal or 'Unspecified'}\n\n"
            f"Observed issue:\n{issue}\n\n"
            "Use this evidence to choose the next action. Avoid repeating the same approach unless you can explain what changed."
        )
    if evaluation.required_next_mode == "replan" or evaluation.status == "stalled":
        return (
            "Progress appears stalled.\n\n"
            "Reassess the goal, the strongest observed evidence, the active blocker and the previous failed approach. "
            "Choose a materially different next action that reduces uncertainty or advances the goal."
        )
    if evaluation.required_next_mode == "observe_result" or evaluation.status == "unverified":
        return (
            "You have taken actions intended to advance the task, but there is not yet clear observed evidence that the current approach is working.\n\n"
            "Inspect the current result using the most informative available method for this task. Use what you observe to choose the next step."
        )
    return (
        "Ground the next step in the semantic evidence gathered so far. Address missing evidence or blockers before making unsupported claims."
    )


def record_intervention(state: EvidenceLoopState, *, turn_id: int, kind: str, message: str, evaluation: EvidenceEvaluation) -> None:
    state.interventions.append(InterventionRecord(turn_id=turn_id, kind=kind, message=message, evaluation=asdict(evaluation)))
