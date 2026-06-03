from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATIF_SCHEMA_VERSION = "ATIF-v1.7"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", "")))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _response_message(response: dict[str, Any]) -> str:
    return _text_from_content(response.get("content", []))


def _tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for block in response.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            calls.append({
                "tool_call_id": str(block.get("id") or f"tool-{len(calls)+1}"),
                "function_name": str(block.get("name") or "tool"),
                "arguments": block.get("input") if isinstance(block.get("input"), dict) else {},
            })
    return calls


def _observation_from_result(result: Any, fallback_id: str | None = None) -> dict[str, Any]:
    if isinstance(result, dict):
        content = result.get("content")
        if content is None:
            content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        return {
            "source_call_id": str(result.get("tool_use_id") or result.get("id") or fallback_id) if (result.get("tool_use_id") or result.get("id") or fallback_id) else None,
            "content": str(content),
            "extra": {k: v for k, v in result.items() if k not in {"content"}},
        }
    return {"source_call_id": fallback_id, "content": str(result)}


def export_atif_trajectory(
    *,
    transcript: dict[str, Any],
    telemetry: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    agent_meta = telemetry.get("agent") if isinstance(telemetry.get("agent"), dict) else {}
    model_meta = telemetry.get("model") if isinstance(telemetry.get("model"), dict) else {}
    usage = telemetry.get("usage") if isinstance(telemetry.get("usage"), dict) else {}
    timing = telemetry.get("timing") if isinstance(telemetry.get("timing"), dict) else {}
    outcome = telemetry.get("outcome") if isinstance(telemetry.get("outcome"), dict) else {}
    termination = telemetry.get("termination") if isinstance(telemetry.get("termination"), dict) else {}

    steps: list[dict[str, Any]] = []
    instruction = transcript.get("instruction") or transcript.get("objective") or ""
    if instruction:
        steps.append({"step_id": len(steps) + 1, "timestamp": _now(), "source": "user", "message": str(instruction)})

    responses = [r for r in transcript.get("responses", []) if isinstance(r, dict)]
    tool_results = list(transcript.get("tool_results", []) or [])
    result_idx = 0
    for response in responses:
        calls = _tool_calls(response)
        usage_payload = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        metrics: dict[str, Any] = {}
        prompt_tokens = usage_payload.get("input_tokens", usage_payload.get("prompt_tokens"))
        completion_tokens = usage_payload.get("output_tokens", usage_payload.get("completion_tokens"))
        if isinstance(prompt_tokens, int):
            metrics["prompt_tokens"] = prompt_tokens
        if isinstance(completion_tokens, int):
            metrics["completion_tokens"] = completion_tokens
        step: dict[str, Any] = {
            "step_id": len(steps) + 1,
            "timestamp": _now(),
            "source": "agent",
            "model_name": response.get("model") or model_meta.get("identifier"),
            "message": _response_message(response),
            "llm_call_count": 1,
            "extra": {"stop_reason": response.get("stop_reason")},
        }
        if calls:
            step["tool_calls"] = calls
        if metrics:
            step["metrics"] = metrics
        reasoning = response.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            step["reasoning_content"] = reasoning
        steps.append(step)
        if calls:
            obs_results = []
            for call in calls:
                if result_idx < len(tool_results):
                    obs_results.append(_observation_from_result(tool_results[result_idx], call["tool_call_id"]))
                    result_idx += 1
            if obs_results:
                steps.append({
                    "step_id": len(steps) + 1,
                    "timestamp": _now(),
                    "source": "system",
                    "message": "tool observations",
                    "observation": {"results": obs_results},
                })

    for event in events or []:
        etype = str(event.get("type") or event.get("event_type") or "")
        if etype in {"tool_finished", "tool_result"}:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
            steps.append({
                "step_id": len(steps) + 1,
                "timestamp": str(event.get("ts") or _now()),
                "source": "system",
                "message": etype,
                "observation": {"results": [{"source_call_id": str(payload.get("tool_use_id") or payload.get("tool_call_id") or "") or None, "content": json.dumps(payload, ensure_ascii=False)}]},
                "extra": {"villani_event_type": etype},
            })

    if not steps:
        steps.append({"step_id": 1, "timestamp": _now(), "source": "system", "message": "no transcript content available"})

    final_metrics: dict[str, Any] = {"total_steps": len(steps), "extra": {"timing": timing}}
    if usage.get("quality") == "exact":
        final_metrics["total_prompt_tokens"] = usage.get("input_tokens")
        final_metrics["total_completion_tokens"] = usage.get("output_tokens")

    return {
        "schema_version": ATIF_SCHEMA_VERSION,
        "session_id": telemetry.get("attempt_id"),
        "trajectory_id": telemetry.get("attempt_id"),
        "agent": {
            "name": str(agent_meta.get("name") or "villani"),
            "version": str(agent_meta.get("version") or "unknown"),
            "model_name": model_meta.get("identifier"),
            "extra": {"provider": model_meta.get("provider")},
        },
        "steps": steps,
        "final_metrics": final_metrics,
        "extra": {"outcome": outcome, "termination": termination},
    }


def write_atif_trajectory(path: Path, *, transcript: dict[str, Any], telemetry: dict[str, Any], events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    trajectory = export_atif_trajectory(transcript=transcript, telemetry=telemetry, events=events)
    path.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False), encoding="utf-8")
    return trajectory
