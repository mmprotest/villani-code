from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from villani_code.trace_summary import normalize_token_usage
from villani_code.utils import ensure_dir

REQUIRED_RUN_ARTIFACTS = [
    "telemetry.json",
    "full_transcript.json",
    "trajectory.json",
    "runtime_events.jsonl",
    "model_requests.jsonl",
    "model_responses.jsonl",
    "run_meta.json",
]

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|authorization|auth[_-]?token|access[_-]?token|refresh[_-]?token|bearer|password|passwd|secret|private[_-]?key|client[_-]?secret)",
    re.I,
)
_TOKEN_USAGE_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "tokens_input",
    "tokens_output",
    "tokens_total",
    "total_prompt_tokens",
    "total_completion_tokens",
}
_SECRET_VALUE_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----)"
    r"|(Bearer\s+[A-Za-z0-9._~+\-/=]{10,})"
    r"|(sk-[A-Za-z0-9_\-]{12,}|xox[baprs]-[A-Za-z0-9_\-]{10,})"
    r"|((?:authorization)\s*[:=]\s*Bearer\s+[A-Za-z0-9._~+\-/=]{10,})"
    r"|((?:api[_-]?key|apikey|auth[_-]?token|access[_-]?token|refresh[_-]?token|password|passwd|secret|private[_-]?key|client[_-]?secret)\s*[:=]\s*['\"]?[^\s'\",;}]+)"
    r"|(^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|APIKEY|AUTH_TOKEN|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD|PASSWD|SECRET|PRIVATE_KEY|CLIENT_SECRET)[A-Z0-9_]*\s*=\s*[^\r\n]+)",
    re.I | re.S | re.M,
)

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_text(value: str) -> str:
    return _SECRET_VALUE_RE.sub("[REDACTED_SECRET]", value)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _SECRET_KEY_RE.search(key) and key not in _TOKEN_USAGE_KEYS:
                out[key] = "[REDACTED_SECRET]"
            else:
                out[key] = sanitize_payload(v)
        return out
    if isinstance(value, list):
        return [sanitize_payload(v) for v in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(sanitize_payload(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sanitize_payload(payload), ensure_ascii=False) + "\n")


def usage_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    # Run totals are exact only when every completed model response in this run
    # carries provider-returned exact usage. Failed requests (model_exception) did
    # not produce completed responses and therefore do not contribute fake usage.
    completed = [e for e in events if e.get("event_type") in {"model_response", "model_request_completed"}]
    if not completed:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
    sums = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for event in completed:
        input_tokens = event.get("input_tokens", event.get("tokens_input"))
        output_tokens = event.get("output_tokens", event.get("tokens_output"))
        total_tokens = event.get("total_tokens", event.get("tokens_total"))
        if event.get("usage_quality") != "exact" or input_tokens is None or output_tokens is None or total_tokens is None:
            return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
        sums["input_tokens"] += int(input_tokens)
        sums["output_tokens"] += int(output_tokens)
        sums["total_tokens"] += int(total_tokens)
    return {**sums, "quality": "exact"}


def canonical_artifact_dir(repo: Path, mission_id: str | None = None, debug_root: Path | None = None) -> Path:
    if debug_root is not None:
        root = debug_root.expanduser().resolve()
        if mission_id:
            return root / mission_id
        return root
    root = repo.resolve() / ".villani_code"
    if mission_id:
        return root / "missions" / mission_id
    current = root / "missions" / "current.json"
    if current.exists():
        try:
            mid = str(json.loads(current.read_text(encoding="utf-8")).get("mission_id", ""))
            if mid:
                return root / "missions" / mid
        except Exception:
            pass
    return root


def write_full_transcript(run_dir: Path, *, run_id: str, instruction: str, terminal: dict[str, Any] | None = None) -> Path:
    events = read_jsonl(run_dir / "events.jsonl")
    runtime_events = read_jsonl(run_dir / "runtime_events.jsonl")
    model_requests = read_jsonl(run_dir / "model_requests.jsonl")
    model_responses = read_jsonl(run_dir / "model_responses.jsonl")
    ordered: list[dict[str, Any]] = []
    if instruction:
        ordered.append({"type": "user_instruction", "content": instruction})
    for row in events:
        etype = row.get("event_type")
        if etype in {"model_request_started", "model_request_completed"}:
            continue
        payload = row.get("payload", {})
        item: dict[str, Any] = {"type": etype, "ts": row.get("ts"), "turn_index": row.get("turn_index"), "payload": payload}
        if etype == "tool_call_started":
            item["type"] = "tool_invocation"
        elif etype in {"tool_call_completed", "tool_call_failed"}:
            item["type"] = "tool_observation"
        elif str(etype).startswith("validation"):
            item["type"] = "verification"
        ordered.append(item)
    for row in model_requests:
        ordered.append({"type": "model_request", "ts": row.get("ts"), "request_id": row.get("request_id"), "payload": row.get("payload", {})})
    for row in model_responses:
        kind = "model_exception" if row.get("event_type") == "model_exception" else "assistant_response"
        ordered.append({"type": kind, "ts": row.get("ts"), "request_id": row.get("request_id"), "payload": row})
    ordered[1:] = sorted(ordered[1:], key=lambda item: str(item.get("ts") or ""))
    if terminal:
        ordered.append({"type": "terminal_state", "payload": terminal})
    payload = {"schema_version": "villani.full_transcript.v1", "run_id": run_id, "events": ordered, "runtime_events": runtime_events}
    path = run_dir / "full_transcript.json"
    write_json(path, payload)
    return path


def _content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "tool_result":
                    parts.append(_content_to_text(item.get("content", "")))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        if value.get("type") == "text":
            return str(value.get("text", ""))
        return _content_to_text(value.get("content", ""))
    return "" if value is None else str(value)


def _assistant_message_from_response(row: dict[str, Any]) -> str:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    content = payload.get("content") if isinstance(payload, dict) else None
    return _content_to_text(content)



def _response_content_blocks(row: dict[str, Any]) -> list[dict[str, Any]]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    content = payload.get("content") if isinstance(payload, dict) else []
    return [item for item in content if isinstance(item, dict)] if isinstance(content, list) else []


def _tool_calls_from_response(row: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for block in _response_content_blocks(row):
        if block.get("type") != "tool_use":
            continue
        call_id = str(block.get("id") or block.get("tool_call_id") or "")
        calls.append(
            {
                "tool_call_id": call_id,
                "function_name": str(block.get("name") or block.get("function_name") or ""),
                "arguments": block.get("input") if isinstance(block.get("input"), dict) else {},
            }
        )
    return calls

def _tool_result_content(payload: dict[str, Any]) -> str:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if "content" in result:
        return _content_to_text(result.get("content"))
    result_summary = payload.get("result_summary") if isinstance(payload.get("result_summary"), dict) else {}
    for key in ("stdout", "stdout_preview", "preview", "content", "summary"):
        if result.get(key) is not None:
            return _content_to_text(result.get(key))
        if result_summary.get(key) is not None:
            return _content_to_text(result_summary.get(key))
    if payload.get("summary") is not None:
        return _content_to_text(payload.get("summary"))
    return ""


def _telemetry_usage(run_dir: Path) -> dict[str, Any]:
    telemetry_path = run_dir / "telemetry.json"
    if telemetry_path.exists():
        try:
            telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
            usage = telemetry.get("usage") if isinstance(telemetry.get("usage"), dict) else {}
            return {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "quality": usage.get("quality", "unavailable"),
            }
        except Exception:
            pass
    return usage_from_events(read_jsonl(run_dir / "model_responses.jsonl"))


def write_trajectory(run_dir: Path, *, run_id: str, mission_id: str | None, agent_version: str | None, model: str | None, provider: str | None, terminal: dict[str, Any] | None = None) -> Path:
    if not isinstance(agent_version, str) or not agent_version:
        from villani_code import __version__ as agent_version
    transcript_path = run_dir / "full_transcript.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8")) if transcript_path.exists() else {"events": []}
    events = transcript.get("events", []) if isinstance(transcript.get("events"), list) else []
    telemetry_usage = _telemetry_usage(run_dir)
    steps: list[dict[str, Any]] = []
    pending_tool_calls: dict[str, dict[str, Any]] = {}
    pending_observations: dict[str, list[dict[str, Any]]] = {}
    tool_call_steps: dict[str, dict[str, Any]] = {}

    def next_step_id() -> int:
        return len(steps) + 1

    def attach_observation(step: dict[str, Any], observation: dict[str, Any]) -> None:
        step.setdefault("observation", {}).setdefault("results", []).append(observation)

    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind == "user_instruction":
            step: dict[str, Any] = {"step_id": next_step_id(), "source": "user", "message": str(event.get("content", ""))}
            if event.get("ts") is not None:
                step["timestamp"] = event.get("ts")
            steps.append(step)
            continue
        if kind == "assistant_response":
            row = payload
            step = {
                "step_id": next_step_id(),
                "source": "agent",
                "model_name": model,
                "message": _assistant_message_from_response(row),
            }
            if event.get("ts") is not None:
                step["timestamp"] = event.get("ts")
            tool_calls = _tool_calls_from_response(row)
            if tool_calls:
                step["tool_calls"] = tool_calls
                for call in tool_calls:
                    call_id = str(call.get("tool_call_id") or "")
                    if call_id:
                        tool_call_steps[call_id] = step
                        for observation in pending_observations.pop(call_id, []):
                            attach_observation(step, observation)
            if row.get("usage_quality") == "exact":
                metrics: dict[str, int] = {}
                if row.get("input_tokens") is not None:
                    metrics["prompt_tokens"] = int(row.get("input_tokens"))
                if row.get("output_tokens") is not None:
                    metrics["completion_tokens"] = int(row.get("output_tokens"))
                if metrics:
                    step["metrics"] = metrics
            steps.append(step)
            continue
        if kind == "model_exception":
            message = sanitize_text(str(payload.get("exception_message") or ""))
            step = {"step_id": next_step_id(), "source": "system", "message": message}
            if event.get("ts") is not None:
                step["timestamp"] = event.get("ts")
            steps.append(step)
            continue
        if kind == "tool_invocation":
            call_id = str(payload.get("tool_call_id") or "")
            if call_id in tool_call_steps:
                continue
            pending_tool_calls[call_id] = {
                "ts": event.get("ts"),
                "tool_call_id": call_id,
                "function_name": str(payload.get("tool_name") or ""),
                "arguments": payload.get("args") if isinstance(payload.get("args"), dict) else {},
            }
            continue
        if kind == "tool_observation":
            call_id = str(payload.get("tool_call_id") or "")
            observation = {"source_call_id": call_id, "content": _tool_result_content(payload)}
            origin_step = tool_call_steps.get(call_id)
            if origin_step is not None:
                attach_observation(origin_step, observation)
                continue
            pending_observations.setdefault(call_id, []).append(observation)
            call = pending_tool_calls.pop(call_id, None)
            if call is None:
                continue
            observations = pending_observations.pop(call_id, [])
            step = {
                "step_id": next_step_id(),
                "source": "agent",
                "model_name": model,
                "message": "",
                "tool_calls": [
                    {
                        "tool_call_id": call["tool_call_id"],
                        "function_name": call["function_name"],
                        "arguments": call["arguments"],
                    }
                ],
            }
            if call.get("ts") is not None:
                step["timestamp"] = call.get("ts")
            if observations:
                step["observation"] = {"results": observations}
            steps.append(step)
            continue
        if kind == "terminal_state":
            continue

    for call_id, call in pending_tool_calls.items():
        if call_id in tool_call_steps:
            continue
        step = {
            "step_id": next_step_id(),
            "source": "agent",
            "model_name": model,
            "message": "",
            "tool_calls": [
                {
                    "tool_call_id": call["tool_call_id"],
                    "function_name": call["function_name"],
                    "arguments": call["arguments"],
                }
            ],
        }
        if call.get("ts") is not None:
            step["timestamp"] = call.get("ts")
        observations = pending_observations.get(call_id, [])
        if observations:
            step["observation"] = {"results": observations}
        steps.append(step)

    quality = telemetry_usage.get("quality")
    final_metrics = {
        "total_prompt_tokens": telemetry_usage.get("input_tokens") if quality == "exact" else None,
        "total_completion_tokens": telemetry_usage.get("output_tokens") if quality == "exact" else None,
        "total_steps": len(steps),
    }
    extra: dict[str, Any] = {"verified_outcome": "unverified"}
    if terminal:
        extra.update(sanitize_payload(terminal))
        if terminal.get("verified_outcome") is not None:
            extra["verified_outcome"] = terminal.get("verified_outcome")
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": mission_id or run_id,
        "agent": {"name": "villani-code", "version": agent_version, "model_name": model, "extra": {"provider": provider}},
        "steps": steps,
        "final_metrics": final_metrics,
        "extra": extra,
    }
    path = run_dir / "trajectory.json"
    write_json(path, trajectory)
    return path
