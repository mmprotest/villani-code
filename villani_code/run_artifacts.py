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

_SECRET_KEY_RE = re.compile(r"(api[_-]?key|authorization|bearer|token|secret|password|credential)", re.I)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_\-]{12,}|xox[baprs]-[A-Za-z0-9_\-]{10,}|Bearer\s+[A-Za-z0-9._\-]{10,}|api[_-]?key\s*[=:]\s*[^\s]+)"
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
            if _SECRET_KEY_RE.search(key) and key not in {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens", "tokens_input", "tokens_output", "tokens_total", "total_prompt_tokens", "total_completion_tokens"}:
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


def _known_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def usage_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [e for e in events if e.get("event_type") in {"model_response", "model_request_completed"}]
    if not completed:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
    sums = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for event in completed:
        input_tokens = _known_int(event.get("input_tokens") if event.get("input_tokens") is not None else event.get("tokens_input"))
        output_tokens = _known_int(event.get("output_tokens") if event.get("output_tokens") is not None else event.get("tokens_output"))
        total_tokens = _known_int(event.get("total_tokens") if event.get("total_tokens") is not None else event.get("tokens_total"))
        if input_tokens is None or output_tokens is None:
            return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens
        sums["input_tokens"] += input_tokens
        sums["output_tokens"] += output_tokens
        sums["total_tokens"] += total_tokens
    return {**sums, "quality": "exact"}


def canonical_artifact_dir(repo: Path, mission_id: str | None = None) -> Path:
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


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        return _message_text(content.get("content"))
    return ""


def write_trajectory(run_dir: Path, *, run_id: str, mission_id: str | None, agent_version: str | None, model: str | None, provider: str | None, terminal: dict[str, Any] | None = None) -> Path:
    transcript_path = run_dir / "full_transcript.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8")) if transcript_path.exists() else {"events": []}
    model_events = read_jsonl(run_dir / "model_responses.jsonl")
    usage = usage_from_events([r for r in model_events if isinstance(r, dict)])
    steps: list[dict[str, Any]] = []
    next_step = 1
    for event in transcript.get("events", []):
        kind = event.get("type")
        payload = event.get("payload", {}) if isinstance(event.get("payload", {}), dict) else {}
        step: dict[str, Any] | None = None
        if kind == "user_instruction":
            step = {"source": "user", "message": str(event.get("content", ""))}
        elif kind == "assistant_response":
            response_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
            step = {"source": "agent", "message": _message_text(response_payload.get("content")), "model_name": payload.get("model_identifier") or model}
            prompt_tokens = _known_int(payload.get("input_tokens"))
            completion_tokens = _known_int(payload.get("output_tokens"))
            if prompt_tokens is not None and completion_tokens is not None:
                step["metrics"] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
        elif kind == "tool_invocation":
            step = {
                "source": "agent",
                "tool_calls": [
                    {
                        "tool_call_id": payload.get("tool_call_id"),
                        "function_name": payload.get("tool_name"),
                        "arguments": payload.get("args", {}),
                    }
                ],
            }
        elif kind == "tool_observation":
            step = {
                "source": "environment",
                "observation": {
                    "source_call_id": payload.get("tool_call_id"),
                    "results": payload.get("result") if payload.get("result") is not None else payload.get("summary"),
                },
            }
        elif kind == "model_exception":
            step = {"source": "agent", "message": "", "model_name": payload.get("model_identifier") or model, "extra": {"exception_type": payload.get("exception_type"), "exception_message": payload.get("exception_message")}}
        elif kind == "terminal_state":
            step = {"source": "system", "message": str(payload.get("termination_reason") or payload.get("status") or "terminal"), "extra": payload}
        if step is None:
            continue
        step["step_id"] = f"step-{next_step}"
        if event.get("ts") is not None:
            step["timestamp"] = event.get("ts")
        steps.append(step)
        next_step += 1
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": mission_id or run_id,
        "agent": {"name": "villani-code", "version": agent_version, "model_name": model, "extra": {"provider": provider}},
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": usage["input_tokens"],
            "total_completion_tokens": usage["output_tokens"],
            "total_steps": len(steps),
        },
        "extra": {**(terminal or {}), "usage_quality": usage["quality"], "total_tokens": usage["total_tokens"]},
    }
    path = run_dir / "trajectory.json"
    write_json(path, trajectory)
    return path
