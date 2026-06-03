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
            if _SECRET_KEY_RE.search(key) and key not in {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens", "tokens_input", "tokens_output", "tokens_total"}:
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
    exact = [e for e in events if e.get("event_type") in {"model_response", "model_request_completed"} and e.get("usage_quality") == "exact"]
    if not exact:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}
    sums = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for event in exact:
        sums["input_tokens"] += int(event.get("input_tokens") or event.get("tokens_input") or 0)
        sums["output_tokens"] += int(event.get("output_tokens") or event.get("tokens_output") or 0)
        sums["total_tokens"] += int(event.get("total_tokens") or event.get("tokens_total") or 0)
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


def write_trajectory(run_dir: Path, *, run_id: str, mission_id: str | None, agent_version: str | None, model: str | None, provider: str | None, terminal: dict[str, Any] | None = None) -> Path:
    transcript_path = run_dir / "full_transcript.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8")) if transcript_path.exists() else {"events": []}
    model_events = read_jsonl(run_dir / "model_responses.jsonl")
    usage = usage_from_events([r for r in model_events if isinstance(r, dict)])
    steps: list[dict[str, Any]] = []
    for idx, event in enumerate(transcript.get("events", []), start=1):
        kind = event.get("type")
        payload = event.get("payload", {})
        step: dict[str, Any] = {"index": idx, "type": kind, "timestamp": event.get("ts")}
        if kind == "user_instruction":
            step["role"] = "user"
            step["content"] = event.get("content", "")
        elif kind == "assistant_response":
            step["role"] = "assistant"
            step["content"] = payload.get("response") or payload.get("content") or payload
            metrics = {k: payload.get(k) for k in ("input_tokens", "output_tokens", "total_tokens") if payload.get(k) is not None}
            if metrics:
                step["metrics"] = metrics
        elif kind == "tool_invocation":
            step["tool_calls"] = [{"id": payload.get("tool_call_id"), "name": payload.get("tool_name"), "arguments": payload.get("args", {})}]
        elif kind == "tool_observation":
            step["observations"] = [{"tool_call_id": payload.get("tool_call_id"), "status": payload.get("status"), "content": payload.get("result") or payload.get("summary")}]
        else:
            step["extra"] = payload
        steps.append(step)
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": mission_id or run_id,
        "agent": {"name": "villani-code", "version": agent_version, "model": model, "provider": provider},
        "steps": steps,
        "metrics": {"usage": usage},
        "final_metadata": terminal or {},
    }
    path = run_dir / "trajectory.json"
    write_json(path, trajectory)
    return path
