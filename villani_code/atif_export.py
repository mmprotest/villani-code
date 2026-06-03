from __future__ import annotations

import copy
import json
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
        arguments = block.get("input", {})
        calls.append(
            {
                "tool_call_id": block.get("id"),
                "function_name": block.get("name"),
                "arguments": _deepcopy_jsonable(arguments) if isinstance(arguments, dict) else {},
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


def _atif_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _observation_for_tool_calls(tool_call_ids: set[str], tool_results: list[Any], redact: bool) -> dict[str, Any] | None:
    results: list[dict[str, Any]] = []
    for result in tool_results:
        call_id = _tool_result_call_id(result)
        if call_id is None or call_id not in tool_call_ids:
            continue
        payload = _redact_tool_result_value(_deepcopy_jsonable(result)) if redact else _deepcopy_jsonable(result)
        content = payload.get("content") if isinstance(payload, dict) and "content" in payload else payload
        results.append({"source_call_id": call_id, "content": _atif_content(content)})
    if not results:
        return None
    return {"results": results}


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
    total_prompt_tokens = 0
    total_completion_tokens = 0
    saw_prompt_tokens = False
    saw_completion_tokens = False

    def append_step(step: dict[str, Any]) -> None:
        step["step_id"] = len(steps) + 1
        steps.append(step)

    for idx, response in enumerate(responses):
        request = requests[idx] if idx < len(requests) and isinstance(requests[idx], dict) else {}
        system_prompt = request.get("system")
        if not system_emitted and system_prompt:
            append_step({"source": "system", "message": str(system_prompt)})
            system_emitted = True

        current_messages = request.get("messages", []) if isinstance(request.get("messages"), list) else []
        for message in _new_messages_since(previous_request_messages, current_messages):
            if not isinstance(message, dict) or _is_tool_result_only_message(message):
                continue
            role = str(message.get("role", "user"))
            if role == "assistant":
                continue
            source = role if role in {"system", "user"} else "user"
            content = _redact_messages([message], redact)[0].get("content") if redact else _deepcopy_jsonable(message.get("content"))
            text = _content_text(content)
            append_step({"source": source, "message": text if text is not None else _atif_content(content)})
        previous_request_messages = _deepcopy_jsonable(current_messages)

        response_content = response.get("content", []) if isinstance(response, dict) else []
        tool_calls = _tool_calls_from_content(response_content)
        usage = normalize_token_usage(response if isinstance(response, dict) else {})
        metrics: dict[str, Any] = {}
        if usage.get("tokens_input") is not None:
            metrics["prompt_tokens"] = usage.get("tokens_input")
            total_prompt_tokens += int(usage["tokens_input"] or 0)
            saw_prompt_tokens = True
        if usage.get("tokens_output") is not None:
            metrics["completion_tokens"] = usage.get("tokens_output")
            total_completion_tokens += int(usage["tokens_output"] or 0)
            saw_completion_tokens = True

        agent_step: dict[str, Any] = {
            "source": "agent",
            "message": _content_text(response_content) or "",
            "llm_call_count": 1,
        }
        if metrics:
            agent_step["metrics"] = metrics
        if tool_calls:
            agent_step["tool_calls"] = tool_calls
            observation = _observation_for_tool_calls(
                {str(call.get("tool_call_id")) for call in tool_calls if call.get("tool_call_id") is not None}, tool_results, redact
            )
            if observation is not None:
                agent_step["observation"] = observation
        append_step(agent_step)

    final_metrics: dict[str, Any] = {"total_steps": len(steps)}
    if saw_prompt_tokens:
        final_metrics["total_prompt_tokens"] = total_prompt_tokens
    if saw_completion_tokens:
        final_metrics["total_completion_tokens"] = total_completion_tokens

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
