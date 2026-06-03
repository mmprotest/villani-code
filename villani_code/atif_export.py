from __future__ import annotations

import copy
from typing import Any

from villani_code.trace_summary import normalize_token_usage
from villani_code.transcripts import maybe_redact_payload

_REDACTED_TOOL_RESULT_CONTENT = "[REDACTED_TOOL_RESULT_CONTENT]"


def _deepcopy_jsonable(value: Any) -> Any:
    return copy.deepcopy(value)


def _redact_tool_result_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {k: _redact_tool_result_value(v) for k, v in value.items()}
        if "content" in redacted:
            redacted["content"] = _REDACTED_TOOL_RESULT_CONTENT
        if "result" in redacted:
            redacted["result"] = _REDACTED_TOOL_RESULT_CONTENT
        return redacted
    if isinstance(value, list):
        return [_redact_tool_result_value(v) for v in value]
    return value


def _redact_messages(messages: list[dict[str, Any]], redact: bool) -> list[dict[str, Any]]:
    payload = maybe_redact_payload({"messages": _deepcopy_jsonable(messages)}, redact)
    redacted_messages = payload.get("messages", [])
    return redacted_messages if isinstance(redacted_messages, list) else []


def _redact_requests(requests: list[dict[str, Any]], redact: bool) -> list[dict[str, Any]]:
    if not redact:
        return _deepcopy_jsonable(requests)
    return [maybe_redact_payload(_deepcopy_jsonable(request), True) for request in requests]


def _redact_tool_results(results: list[Any], redact: bool) -> list[Any]:
    copied = _deepcopy_jsonable(results)
    if not redact:
        return copied
    return [_redact_tool_result_value(result) for result in copied]


def _content_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = [str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"]
    text = "\n".join(part for part in parts if part)
    return text if text else None


def _tool_calls_from_content(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        calls.append(
            {
                "id": block.get("id"),
                "name": block.get("name"),
                "arguments": _deepcopy_jsonable(block.get("input", {})),
            }
        )
    return calls


def _tool_result_call_id(result: Any) -> str | None:
    if isinstance(result, dict):
        for key in ("tool_use_id", "tool_call_id", "id"):
            value = result.get(key)
            if value is not None:
                return str(value)
    return None


def _tool_results_with_call_ids(transcript: dict[str, Any]) -> list[Any]:
    raw_results = transcript.get("tool_results", []) if isinstance(transcript.get("tool_results"), list) else []
    invocations = transcript.get("tool_invocations", []) if isinstance(transcript.get("tool_invocations"), list) else []
    enriched: list[Any] = []
    for idx, result in enumerate(raw_results):
        copied = _deepcopy_jsonable(result)
        if isinstance(copied, dict) and _tool_result_call_id(copied) is None and idx < len(invocations) and isinstance(invocations[idx], dict):
            call_id = invocations[idx].get("id") or invocations[idx].get("tool_call_id") or invocations[idx].get("tool_use_id")
            if call_id is not None:
                copied["tool_call_id"] = str(call_id)
        enriched.append(copied)
    return enriched


def _observations_for_tool_calls(tool_call_ids: set[str], tool_results: list[Any], redact: bool) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for result in tool_results:
        call_id = _tool_result_call_id(result)
        if call_id is None or call_id not in tool_call_ids:
            continue
        payload = _redact_tool_result_value(_deepcopy_jsonable(result)) if redact else _deepcopy_jsonable(result)
        observations.append({"tool_call_id": call_id, "result": payload})
    return observations


def _is_tool_result_only_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    return all(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def _new_messages_since(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(current) >= len(previous) and current[: len(previous)] == previous:
        return current[len(previous) :]
    return current


def build_full_transcript_artifact(
    *,
    run_id: str,
    model: str | None,
    provider: str | None,
    transcript: dict[str, Any],
    messages: list[dict[str, Any]],
    status: str,
    termination_reason: str | None,
    redact: bool,
) -> dict[str, Any]:
    requests = transcript.get("requests", []) if isinstance(transcript.get("requests"), list) else []
    responses = transcript.get("responses", []) if isinstance(transcript.get("responses"), list) else []
    tool_invocations = transcript.get("tool_invocations", []) if isinstance(transcript.get("tool_invocations"), list) else []
    tool_results = _tool_results_with_call_ids(transcript)
    return {
        "schema_version": "villani-debug-transcript-v1",
        "run_id": run_id,
        "runtime_mode": "execution",
        "model": model,
        "provider": provider,
        "status": status,
        "termination_reason": termination_reason,
        "messages": _redact_messages(messages, redact),
        "requests": _redact_requests(requests, redact),
        "responses": _deepcopy_jsonable(responses),
        "tool_invocations": _deepcopy_jsonable(tool_invocations),
        "tool_results": _redact_tool_results(tool_results, redact),
        "streamed_events_count": transcript.get("streamed_events_count", 0),
    }


def build_atif_trajectory(
    *,
    run_id: str,
    agent_version: str,
    model: str | None,
    provider: str | None,
    transcript: dict[str, Any],
    messages: list[dict[str, Any]],
    status: str,
    termination_reason: str | None,
    redact: bool,
) -> dict[str, Any]:
    del messages  # ATIF dialogue steps are derived from captured request/response pairs.
    requests = transcript.get("requests", []) if isinstance(transcript.get("requests"), list) else []
    responses = transcript.get("responses", []) if isinstance(transcript.get("responses"), list) else []
    tool_results = _tool_results_with_call_ids(transcript)
    steps: list[dict[str, Any]] = []

    previous_request_messages: list[dict[str, Any]] = []
    system_emitted = False
    total_input = 0
    total_output = 0
    saw_input = False
    saw_output = False
    model_calls = 0

    for idx, response in enumerate(responses):
        request = requests[idx] if idx < len(requests) and isinstance(requests[idx], dict) else {}
        system_prompt = request.get("system")
        if not system_emitted and system_prompt:
            steps.append({"type": "system", "role": "system", "content": system_prompt})
            system_emitted = True

        current_messages = request.get("messages", []) if isinstance(request.get("messages"), list) else []
        for message in _new_messages_since(previous_request_messages, current_messages):
            if not isinstance(message, dict) or _is_tool_result_only_message(message):
                continue
            role = str(message.get("role", "user"))
            content = _redact_messages([message], redact)[0].get("content") if redact else _deepcopy_jsonable(message.get("content"))
            step: dict[str, Any] = {"type": role, "role": role, "content": content}
            text = _content_text(content)
            if text is not None:
                step["message"] = text
            steps.append(step)
        previous_request_messages = _deepcopy_jsonable(current_messages)

        response_content = response.get("content", []) if isinstance(response, dict) else []
        tool_calls = _tool_calls_from_content(response_content)
        usage = normalize_token_usage(response if isinstance(response, dict) else {})
        metrics: dict[str, Any] = {}
        if usage.get("tokens_input") is not None:
            metrics["input_tokens"] = usage.get("tokens_input")
            metrics["prompt_tokens"] = usage.get("tokens_input")
            total_input += int(usage["tokens_input"] or 0)
            saw_input = True
        if usage.get("tokens_output") is not None:
            metrics["output_tokens"] = usage.get("tokens_output")
            metrics["completion_tokens"] = usage.get("tokens_output")
            total_output += int(usage["tokens_output"] or 0)
            saw_output = True
        if usage.get("tokens_total") is not None:
            metrics["total_tokens"] = usage.get("tokens_total")

        model_call: dict[str, Any] = {"model_name": response.get("model", model) if isinstance(response, dict) else model}
        if metrics:
            model_call["metrics"] = dict(metrics)
        agent_step: dict[str, Any] = {
            "type": "agent",
            "role": "assistant",
            "message": _content_text(response_content),
            "content": _deepcopy_jsonable(response_content),
            "tool_calls": tool_calls,
            "observations": _observations_for_tool_calls({str(call.get("id")) for call in tool_calls if call.get("id") is not None}, tool_results, redact),
            "model_calls": [model_call],
        }
        if metrics:
            agent_step["metrics"] = metrics
        steps.append(agent_step)
        model_calls += 1

    final_metrics: dict[str, Any] = {"model_calls": model_calls}
    if saw_input:
        final_metrics["input_tokens"] = total_input
        final_metrics["prompt_tokens"] = total_input
    if saw_output:
        final_metrics["output_tokens"] = total_output
        final_metrics["completion_tokens"] = total_output
    if saw_input or saw_output:
        final_metrics["total_tokens"] = (total_input if saw_input else 0) + (total_output if saw_output else 0)

    return {
        "schema_version": "ATIF-v1.7",
        "session_id": run_id,
        "trajectory_id": run_id,
        "agent": {
            "name": "villani-code",
            "version": agent_version,
            "model_name": model,
            "extra": {"provider": provider, "mode": "regular"},
        },
        "steps": steps,
        "final_metrics": final_metrics,
        "extra": {
            "status": status,
            "termination_reason": termination_reason,
            "transcript_artifact": "transcript.full.json",
        },
    }
