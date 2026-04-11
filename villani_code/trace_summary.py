from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from villani_code.debug_artifacts import DEBUG_JSONL_FILES

AGGREGATION_VERSION = "v2"
TOOL_CALL_SCHEMA_VERSION = "v1"
SHELL_TOOL_NAMES = {"bash", "shell", "sh", "zsh", "ls"}
_MAX_EVENT_TEXT = 2000


class EventLogger:
    """Append-only canonical event logger for a single debug run."""

    def __init__(self, run_id: str, events_path: Path):
        self.run_id = run_id
        self.events_path = events_path
        self._next_event_id = self._discover_next_event_id()

    def _discover_next_event_id(self) -> int:
        if not self.events_path.exists():
            return 1
        next_id = 1
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = row.get("event_id")
            if isinstance(value, int) and value >= next_id:
                next_id = value + 1
        return next_id

    def emit(self, event_type: str, payload: dict[str, Any], turn_index: int | None = None, *, ts: str | None = None) -> dict[str, Any]:
        safe_payload = _sanitize_event_value(payload)
        row: dict[str, Any] = {
            "event_id": self._next_event_id,
            "run_id": self.run_id,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
            "turn_index": turn_index,
            "event_type": event_type,
            "payload": safe_payload,
        }
        self._next_event_id += 1
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row


def _is_binary_like(text: str) -> bool:
    if not text:
        return False
    control_chars = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    return ("\x00" in text) or (control_chars > max(3, len(text) // 20))


def _sanitize_text(text: str) -> str:
    if _is_binary_like(text):
        return "[sanitized-binary-content]"
    if len(text) > _MAX_EVENT_TEXT:
        return text[:_MAX_EVENT_TEXT] + "…[truncated]"
    return text


def _sanitize_event_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return f"[binary-bytes:{len(value)}]"
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {str(k): _sanitize_event_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_event_value(v) for v in value]
    try:
        coerced = str(value)
    except Exception:
        return "[unserializable]"
    return _sanitize_text(coerced)


def load_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not events_path.exists():
        raise FileNotFoundError(f"events file not found: {events_path}")
    for line_number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_number} in {events_path}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"event line {line_number} in {events_path} is not an object")
        events.append(row)
    return events


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_tool_name(name: Any) -> str:
    if not isinstance(name, str):
        return "unknown"
    normalized = name.strip()
    return normalized or "unknown"


def _is_shell_tool(tool_name: str) -> bool:
    return tool_name.strip().lower() in SHELL_TOOL_NAMES


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def normalize_token_usage(payload: dict[str, Any] | None) -> dict[str, int | None]:
    body = payload if isinstance(payload, dict) else {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}

    input_tokens = (
        _safe_int(body.get("input_tokens"))
        or _safe_int(body.get("prompt_tokens"))
        or _safe_int(usage.get("input_tokens"))
        or _safe_int(usage.get("prompt_tokens"))
        or _safe_int(usage.get("prompt_token_count"))
        or _safe_int(usage.get("inputTokenCount"))
    )
    output_tokens = (
        _safe_int(body.get("output_tokens"))
        or _safe_int(body.get("completion_tokens"))
        or _safe_int(usage.get("output_tokens"))
        or _safe_int(usage.get("completion_tokens"))
        or _safe_int(usage.get("output_token_count"))
        or _safe_int(usage.get("outputTokenCount"))
    )
    total_tokens = (
        _safe_int(body.get("total_tokens"))
        or _safe_int(usage.get("total_tokens"))
        or _safe_int(usage.get("totalTokenCount"))
    )

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return {"tokens_input": input_tokens, "tokens_output": output_tokens, "tokens_total": total_tokens}


def normalize_repo_path(raw_path: Any, repo_root: Path | None) -> str:
    value = str(raw_path or "").strip()
    if not value:
        return ""
    normalized = value.replace("\\", "/")
    candidate = Path(normalized)
    if repo_root is not None:
        root = repo_root.resolve()
        if candidate.is_absolute():
            try:
                return candidate.resolve().relative_to(root).as_posix()
            except Exception:
                return candidate.resolve().as_posix()
        abs_candidate = (root / candidate).resolve()
        try:
            return abs_candidate.relative_to(root).as_posix()
        except Exception:
            return normalized.lstrip("./")
    if candidate.is_absolute():
        return candidate.resolve().as_posix()
    return normalized.lstrip("./")


def _extract_repo_root(run_dir: Path) -> Path | None:
    session_meta = run_dir / "session_meta.json"
    if not session_meta.exists():
        return None
    try:
        body = json.loads(session_meta.read_text(encoding="utf-8"))
    except Exception:
        return None
    repo = body.get("repo")
    if isinstance(repo, str) and repo.strip():
        return Path(repo)
    return None


def _truncate(value: Any, limit: int = 240) -> str:
    text = str(value or "")
    return text[:limit]


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _infer_tool_category(name: str) -> str:
    lowered = name.lower()
    if lowered in {"write", "patch", "edit"}:
        return "file_mutation"
    if lowered in {"read", "grep", "glob", "search", "ls"}:
        return "file_read"
    if _is_shell_tool(name):
        return "shell"
    if lowered.startswith("git"):
        return "vcs"
    return "other"


def build_tool_call_records_from_events(run_dir: Path) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    events = load_events(run_dir / "events.jsonl")
    repo_root = _extract_repo_root(run_dir)
    warnings: list[str] = []
    validation_errors: list[str] = []

    started: dict[str, dict[str, Any]] = {}
    terminal_by_id: dict[str, dict[str, Any]] = {}
    command_by_tool_id: dict[str, dict[str, Any]] = {}
    file_event_by_tool_id: dict[str, dict[str, Any]] = {}
    command_tool_ids: set[str] = set()
    file_tool_ids: set[str] = set()

    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        turn_index = event.get("turn_index") if isinstance(event.get("turn_index"), int) else None

        if event_type == "tool_call_started":
            tool_call_id = str(payload.get("tool_call_id", "")).strip()
            if not tool_call_id:
                validation_errors.append("tool_call_started missing tool_call_id")
                continue
            if tool_call_id in started:
                validation_errors.append(f"duplicate tool_call_started for tool_call_id={tool_call_id}")
                continue
            tool_name = _normalize_tool_name(payload.get("tool_name"))
            started[tool_call_id] = {
                "tool_call_id": tool_call_id,
                "run_id": str(event.get("run_id") or run_dir.name),
                "turn_index": turn_index,
                "tool_name": tool_name,
                "tool_category": _infer_tool_category(tool_name),
                "started_at": event.get("ts"),
                "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
            }
            continue

        if event_type in {"tool_call_completed", "tool_call_failed"}:
            tool_call_id = str(payload.get("tool_call_id", "")).strip()
            if not tool_call_id:
                validation_errors.append(f"{event_type} missing tool_call_id")
                continue
            if tool_call_id in terminal_by_id:
                validation_errors.append(f"duplicate terminal tool event for tool_call_id={tool_call_id}")
                continue
            terminal_by_id[tool_call_id] = event
            continue

        if event_type == "command_started":
            tool_call_id = str(payload.get("tool_call_id", "")).strip()
            if not tool_call_id:
                continue
            command_tool_ids.add(tool_call_id)
            command_by_tool_id.setdefault(tool_call_id, {}).update(
                {
                    "command": payload.get("command"),
                    "cwd": payload.get("cwd"),
                }
            )
            continue
        if event_type == "command_finished":
            tool_call_id = str(payload.get("tool_call_id", "")).strip()
            if not tool_call_id:
                continue
            command_tool_ids.add(tool_call_id)
            command_by_tool_id.setdefault(tool_call_id, {}).update(
                {
                    "command": payload.get("command"),
                    "cwd": payload.get("cwd"),
                    "exit_code": payload.get("exit_code"),
                    "stdout_preview": _truncate(payload.get("stdout"), 240),
                    "stderr_preview": _truncate(payload.get("stderr"), 240),
                    "stdout_truncated": _bool(payload.get("stdout_truncated"), _bool(payload.get("truncated"), False)),
                    "stderr_truncated": _bool(payload.get("stderr_truncated"), _bool(payload.get("truncated"), False)),
                }
            )
            continue
        if event_type in {"file_read", "file_write", "file_patch_applied", "file_patch_failed"}:
            tool_call_id = str(payload.get("tool_call_id", "")).strip()
            if not tool_call_id:
                continue
            file_tool_ids.add(tool_call_id)
            normalized_path = normalize_repo_path(payload.get("file_path"), repo_root)
            entry = file_event_by_tool_id.setdefault(tool_call_id, {})
            entry["event_type"] = event_type
            entry["normalized_path"] = normalized_path
            entry["size_bytes"] = payload.get("size_bytes")
            entry["lines_read"] = payload.get("lines_read")
            entry["lines_written"] = payload.get("lines_written")
            entry["preview"] = _truncate(payload.get("preview"), 240) if payload.get("preview") is not None else None
            entry["created"] = payload.get("created")
            entry["overwrote"] = payload.get("overwrote")
            entry["bytes_delta"] = payload.get("bytes_delta")
            entry["lines_added"] = payload.get("lines_added")
            entry["lines_removed"] = payload.get("lines_removed")
            entry["failure_reason"] = payload.get("failure_reason") or payload.get("error")
            entry["hunks_attempted"] = payload.get("hunks_attempted")
            entry["hunks_failed"] = payload.get("hunks_failed")
            continue

    records: list[dict[str, Any]] = []
    all_ids = sorted(started.keys())
    for tool_call_id in all_ids:
        start = started.get(tool_call_id)
        terminal_event = terminal_by_id.get(tool_call_id)
        assert start is not None
        tool_name = _normalize_tool_name(start.get("tool_name"))
        args = start.get("args") if isinstance(start.get("args"), dict) else {}
        normalized_args_summary = {
            "file_path": normalize_repo_path(args.get("file_path"), repo_root) if "file_path" in args else None,
            "command": _truncate(args.get("command"), 160) if "command" in args else None,
            "cwd": normalize_repo_path(args.get("cwd"), repo_root) if "cwd" in args else None,
            "arg_keys": sorted(args.keys()),
        }
        status = "partial"
        ended_at: Any = None
        duration_ms: int | None = None
        result_summary: dict[str, Any] = {"kind": "unterminated_tool_call", "warning": "missing terminal tool event"}
        error_value: dict[str, Any] | None = {"error_type": "missing_terminal_event", "message": "tool_call_started exists without terminal tool event"}
        terminal_payload: dict[str, Any] = {}
        if terminal_event is not None:
            payload = terminal_event.get("payload") if isinstance(terminal_event.get("payload"), dict) else {}
            terminal_payload = payload
            status = "failed" if str(terminal_event.get("event_type")) == "tool_call_failed" else "success"
            ended_at = terminal_event.get("ts")
            error_value = None
            started_at_dt = _parse_ts(start.get("started_at"))
            ended_at_dt = _parse_ts(ended_at)
            if started_at_dt is not None and ended_at_dt is not None:
                duration_ms = max(0, int((ended_at_dt - started_at_dt).total_seconds() * 1000))
            result_summary = payload.get("result_summary") if isinstance(payload.get("result_summary"), dict) else {}
            if not result_summary:
                result_summary = {"summary": _truncate(payload.get("summary"), 240)}
            if status == "failed":
                error_value = payload.get("error") if isinstance(payload.get("error"), dict) else None
                if error_value is None:
                    error_value = {
                        "error_type": payload.get("error_type") or "tool_error",
                        "message": _truncate(payload.get("summary"), 240),
                    }

        lowered = tool_name.lower()
        command_data = command_by_tool_id.get(tool_call_id, {})
        file_data = file_event_by_tool_id.get(tool_call_id, {})
        if _is_shell_tool(tool_name) and command_data:
            result_summary = {
                "kind": "command_result",
                "command": _truncate(command_data.get("command") or args.get("command"), 240),
                "cwd": normalize_repo_path(command_data.get("cwd") or args.get("cwd"), repo_root),
                "exit_code": command_data.get("exit_code"),
                "stdout_preview": command_data.get("stdout_preview", ""),
                "stderr_preview": command_data.get("stderr_preview", ""),
                "stdout_truncated": _bool(command_data.get("stdout_truncated"), False),
                "stderr_truncated": _bool(command_data.get("stderr_truncated"), False),
            }
        elif lowered == "read" and file_data:
            result_summary = {
                "kind": "file_read_result",
                "path": file_data.get("normalized_path"),
                "bytes_read": file_data.get("size_bytes"),
                "lines_read": file_data.get("lines_read"),
                "preview": file_data.get("preview"),
                "preview_truncated": False,
            }
        elif lowered == "write" and file_data:
            result_summary = {
                "kind": "file_write_result",
                "path": file_data.get("normalized_path"),
                "bytes_written": file_data.get("size_bytes"),
                "lines_written": file_data.get("lines_written"),
                "created": file_data.get("created"),
                "overwrote": file_data.get("overwrote"),
            }
        elif lowered == "patch" and file_data:
            result_summary = {
                "kind": "file_patch_result",
                "path": file_data.get("normalized_path"),
                "ok": file_data.get("event_type") == "file_patch_applied",
                "bytes_delta": file_data.get("bytes_delta"),
                "lines_added": file_data.get("lines_added"),
                "lines_removed": file_data.get("lines_removed"),
                "failure_reason": file_data.get("failure_reason"),
                "hunks_attempted": file_data.get("hunks_attempted"),
                "hunks_failed": file_data.get("hunks_failed"),
            }
        elif isinstance(terminal_payload.get("result_summary"), dict):
            terminal_summary = dict(terminal_payload.get("result_summary", {}))
            if "path" in terminal_summary:
                terminal_summary["path"] = normalize_repo_path(terminal_summary.get("path"), repo_root)
            result_summary = terminal_summary

        records.append(
            {
                "tool_call_id": tool_call_id,
                "run_id": start.get("run_id"),
                "turn_index": start.get("turn_index"),
                "tool_name": tool_name,
                "tool_category": _infer_tool_category(tool_name),
                "started_at": start.get("started_at"),
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "status": status,
                "args": args,
                "normalized_args_summary": normalized_args_summary,
                "result_summary": result_summary,
                "error": error_value,
                "schema_version": TOOL_CALL_SCHEMA_VERSION,
            }
        )
    if any(row.get("status") == "partial" for row in records):
        warnings.append("Unterminated tool calls detected.")

    dangling_terminal_ids = sorted(set(terminal_by_id.keys()) - set(started.keys()))
    if dangling_terminal_ids:
        validation_errors.append(
            "terminal tool events contain tool_call_id values without matching tool_call_started: "
            + ", ".join(dangling_terminal_ids[:10])
        )

    tool_ids = {str(row.get("tool_call_id", "")).strip() for row in records}
    bash_tool_ids = {str(row.get("tool_call_id", "")).strip() for row in records if _is_shell_tool(str(row.get("tool_name", "")))}
    missing_command_ids = sorted(command_tool_ids - bash_tool_ids)
    if missing_command_ids:
        validation_errors.append("command events contain tool_call_id values without matching Bash tool rows: " + ", ".join(missing_command_ids[:10]))
    missing_file_ids = sorted(file_tool_ids - tool_ids)
    if missing_file_ids:
        validation_errors.append("file events contain tool_call_id values without matching tool-call records: " + ", ".join(missing_file_ids[:10]))

    return records, warnings, validation_errors


def write_tool_calls_from_events(run_dir: Path) -> Path:
    rows, _, validation_errors = build_tool_call_records_from_events(run_dir)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))
    path = run_dir / "tool_calls.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def aggregate_summary_from_events(run_dir: Path, *, status_override: str | None = None) -> dict[str, Any]:
    events = load_events(run_dir / "events.jsonl")
    repo_root = _extract_repo_root(run_dir)
    warnings: list[str] = []
    validation_errors: list[str] = []

    tool_rows, tool_warnings, tool_errors = build_tool_call_records_from_events(run_dir)
    warnings.extend(tool_warnings)
    validation_errors.extend(tool_errors)

    started_at = None
    ended_at = None
    run_id = ""
    status = status_override

    tool_calls_by_name: Counter[str] = Counter()
    tool_failures_by_name: Counter[str] = Counter()
    tool_started_ids: set[str] = set()
    shell_tools_started_ids: set[str] = set()
    shell_tools_failed_ids: set[str] = set()

    read_count = 0
    write_count = 0
    patch_applied_count = 0
    patch_failed_count = 0
    read_paths: set[str] = set()
    write_paths: set[str] = set()
    touched_paths: set[str] = set()

    model_requests = 0
    model_request_completions = 0
    model_failures = 0
    model_started_request_ids: set[str] = set()
    model_terminal_request_ids: set[str] = set()
    tokens_input_total = 0
    tokens_output_total = 0
    saw_tokens_input = False
    saw_tokens_output = False

    turns: set[int] = set()
    saw_turn_events = False
    run_started_count = 0
    run_terminal_count = 0
    patch_failed_event_tool_ids: set[str] = set()

    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

        maybe_run_id = event.get("run_id")
        if isinstance(maybe_run_id, str) and maybe_run_id:
            run_id = maybe_run_id

        turn_index = event.get("turn_index")
        if isinstance(turn_index, int):
            turns.add(turn_index)
        if event_type in {"turn_started", "turn_finished"}:
            saw_turn_events = True

        ts = _parse_ts(event.get("ts"))
        if ts is not None:
            if started_at is None or ts < started_at:
                started_at = ts
            if ended_at is None or ts > ended_at:
                ended_at = ts

        if event_type in {"run_completed", "run_failed"}:
            run_terminal_count += 1
            status = "completed" if event_type == "run_completed" else "failed"
            continue
        if event_type == "run_started":
            run_started_count += 1
            continue

        if event_type == "tool_call_started":
            tool_id = str(payload.get("tool_call_id", "")).strip()
            tool_name = _normalize_tool_name(payload.get("tool_name"))
            if tool_id:
                tool_started_ids.add(tool_id)
                tool_calls_by_name[tool_name] += 1
                if _is_shell_tool(tool_name):
                    shell_tools_started_ids.add(tool_id)
            continue

        if event_type == "tool_call_failed":
            tool_id = str(payload.get("tool_call_id", "")).strip()
            tool_name = _normalize_tool_name(payload.get("tool_name"))
            tool_failures_by_name[tool_name] += 1
            if tool_id and (_is_shell_tool(tool_name) or tool_id in shell_tools_started_ids):
                shell_tools_failed_ids.add(tool_id)
            continue

        if event_type == "tool_call_completed":
            tool_id = str(payload.get("tool_call_id", "")).strip()
            tool_name = _normalize_tool_name(payload.get("tool_name"))
            exit_code = _safe_int(payload.get("exit_code"))
            if tool_id and (_is_shell_tool(tool_name) or tool_id in shell_tools_started_ids) and exit_code not in {None, 0}:
                shell_tools_failed_ids.add(tool_id)
            continue

        if event_type == "file_read":
            read_count += 1
            path = normalize_repo_path(payload.get("file_path"), repo_root)
            if path:
                read_paths.add(path)
                touched_paths.add(path)
            continue

        if event_type == "file_write":
            write_count += 1
            path = normalize_repo_path(payload.get("file_path"), repo_root)
            if path:
                write_paths.add(path)
                touched_paths.add(path)
            continue

        if event_type == "file_patch_applied":
            patch_applied_count += 1
            path = normalize_repo_path(payload.get("file_path"), repo_root)
            if path:
                write_paths.add(path)
                touched_paths.add(path)
            continue

        if event_type == "file_patch_failed":
            patch_failed_count += 1
            patch_tool_id = str(payload.get("tool_call_id", "")).strip()
            if patch_tool_id:
                patch_failed_event_tool_ids.add(patch_tool_id)
            path = normalize_repo_path(payload.get("file_path"), repo_root)
            if path:
                touched_paths.add(path)
            continue

        if event_type == "model_request_started":
            model_requests += 1
            request_id = str(payload.get("request_id", "")).strip()
            if request_id:
                if request_id in model_started_request_ids:
                    validation_errors.append(f"duplicate model_request_started for request_id={request_id}")
                model_started_request_ids.add(request_id)
            continue

        if event_type == "model_request_failed":
            model_failures += 1
            request_id = str(payload.get("request_id", "")).strip()
            if request_id:
                if request_id not in model_started_request_ids:
                    validation_errors.append(f"model_request_failed references unknown request_id={request_id}")
                if request_id in model_terminal_request_ids:
                    validation_errors.append(f"duplicate terminal model request event for request_id={request_id}")
                model_terminal_request_ids.add(request_id)
            continue

        if event_type == "model_request_completed":
            model_request_completions += 1
            request_id = str(payload.get("request_id", "")).strip()
            if request_id:
                if request_id not in model_started_request_ids:
                    validation_errors.append(f"model_request_completed references unknown request_id={request_id}")
                if request_id in model_terminal_request_ids:
                    validation_errors.append(f"duplicate terminal model request event for request_id={request_id}")
                model_terminal_request_ids.add(request_id)
            in_tokens = _safe_int(payload.get("tokens_input"))
            out_tokens = _safe_int(payload.get("tokens_output"))
            if in_tokens is not None:
                tokens_input_total += in_tokens
                saw_tokens_input = True
            if out_tokens is not None:
                tokens_output_total += out_tokens
                saw_tokens_output = True

    total_tool_calls = len(tool_started_ids)
    if status is None:
        status = "failed" if any(str(e.get("event_type", "")) == "run_failed" for e in events) else "completed"

    started_at_iso = started_at.isoformat() if started_at is not None else None
    ended_at_iso = ended_at.isoformat() if ended_at is not None else None
    duration_ms: int | None = None
    if started_at is not None and ended_at is not None:
        duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))

    artifacts = _build_artifact_manifest(run_dir)

    tokens_input: int | None = tokens_input_total if saw_tokens_input else None
    tokens_output: int | None = tokens_output_total if saw_tokens_output else None

    summary: dict[str, Any] = {
        "run_id": run_id or run_dir.name,
        "status": status,
        "started_at": started_at_iso,
        "ended_at": ended_at_iso,
        "duration_ms": duration_ms,
        "turn_count": len(turns),
        "total_tool_calls": total_tool_calls,
        "tool_calls_by_name": dict(tool_calls_by_name),
        "tool_failures_by_name": dict(tool_failures_by_name),
        "total_file_reads": read_count,
        "total_file_writes": write_count,
        "total_file_patches_applied": patch_applied_count,
        "total_file_patch_failures": patch_failed_count,
        "files_touched": len(touched_paths),
        "unique_files_read": len(read_paths),
        "unique_files_written": len(write_paths),
        "model_requests": model_requests,
        "model_failures": model_failures,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "commands_executed": len(shell_tools_started_ids),
        "commands_failed": len(shell_tools_failed_ids),
        "artifacts": artifacts,
        "aggregation_version": AGGREGATION_VERSION,
    }

    if saw_turn_events and summary["turn_count"] == 0:
        validation_errors.append("Canonical turn events exist but turn_count is zero.")
    if len(shell_tools_started_ids) > 0 and summary["commands_executed"] == 0:
        validation_errors.append("Canonical shell tool calls exist but commands_executed is zero.")
    if total_tool_calls == 0 and any(str(e.get("event_type", "")) == "tool_call_started" for e in events):
        validation_errors.append("tool_call_started events exist while total_tool_calls is zero.")
    if total_tool_calls > 0 and not tool_rows:
        validation_errors.append("Canonical tool_call_started events exist but tool_calls.jsonl materialization is empty.")
    if len(tool_rows) != total_tool_calls:
        validation_errors.append("canonical tool_call_started count does not match materialized tool_call row count.")
    if run_started_count != 1:
        validation_errors.append(f"expected exactly one canonical run_started event; found {run_started_count}.")
    if run_terminal_count != 1:
        validation_errors.append(f"expected exactly one canonical terminal run event; found {run_terminal_count}.")
    if model_request_completions + model_failures > model_requests:
        validation_errors.append(
            "canonical model request terminal event count exceeds model_request_started count."
        )
    if summary["tokens_input"] == 0 and any(
        isinstance((e.get("payload") or {}).get("tokens_input"), int) and (e.get("payload") or {}).get("tokens_input") > 0
        for e in events
        if str(e.get("event_type", "")) == "model_request_completed"
    ):
        validation_errors.append("summary tokens_input is zero despite non-null canonical model usage.")
    if summary["tokens_output"] == 0 and any(
        isinstance((e.get("payload") or {}).get("tokens_output"), int) and (e.get("payload") or {}).get("tokens_output") > 0
        for e in events
        if str(e.get("event_type", "")) == "model_request_completed"
    ):
        validation_errors.append("summary tokens_output is zero despite non-null canonical model usage.")

    tool_calls_artifact = artifacts.get("tool_calls.jsonl", {})
    if tool_calls_artifact.get("exists") and isinstance(tool_calls_artifact.get("path"), str):
        count = sum(1 for _ in Path(tool_calls_artifact["path"]).open("r", encoding="utf-8"))
        if count != total_tool_calls:
            validation_errors.append("tool_calls.jsonl row count differs from summary total_tool_calls.")

    command_tool_ids = {
        str((e.get("payload") or {}).get("tool_call_id", "")).strip()
        for e in events
        if str(e.get("event_type", "")) in {"command_finished", "command_started"}
    }
    command_tool_ids.discard("")
    tool_ids = {str(row.get("tool_call_id", "")).strip() for row in tool_rows}
    bash_tool_ids = {str(row.get("tool_call_id", "")).strip() for row in tool_rows if _is_shell_tool(str(row.get("tool_name", "")))}
    missing_tool_ids = sorted(command_tool_ids - bash_tool_ids)
    if missing_tool_ids:
        validation_errors.append(
            "canonical command events contain tool_call_id values without matching Bash tool-call records: "
            + ", ".join(missing_tool_ids[:10])
        )

    file_tool_ids = {
        str((e.get("payload") or {}).get("tool_call_id", "")).strip()
        for e in events
        if str(e.get("event_type", "")) in {"file_read", "file_write", "file_patch_applied", "file_patch_failed"}
    }
    file_tool_ids.discard("")
    missing_file_tool_ids = sorted(file_tool_ids - tool_ids)
    if missing_file_tool_ids:
        validation_errors.append(
            "canonical file events contain tool_call_id values without matching tool-call records: "
            + ", ".join(missing_file_tool_ids[:10])
        )

    patch_tool_rows = {
        str(row.get("tool_call_id", "")).strip(): row
        for row in tool_rows
        if str(row.get("tool_name", "")).strip().lower() == "patch"
    }
    failed_patch_tool_ids = {
        tool_id
        for tool_id, row in patch_tool_rows.items()
        if str(row.get("status", "")).strip() == "failed"
    }
    missing_patch_failure_events = sorted(failed_patch_tool_ids - patch_failed_event_tool_ids)
    if missing_patch_failure_events:
        validation_errors.append(
            "failed Patch tool-call rows missing canonical file_patch_failed events: "
            + ", ".join(missing_patch_failure_events[:10])
        )
    non_patch_failure_event_ids = sorted(
        tool_id
        for tool_id in patch_failed_event_tool_ids
        if tool_id not in patch_tool_rows
    )
    if non_patch_failure_event_ids:
        validation_errors.append(
            "file_patch_failed events reference tool_call_id values without matching Patch tool-call rows: "
            + ", ".join(non_patch_failure_event_ids[:10])
        )
    if patch_applied_count != sum(1 for e in events if str(e.get("event_type", "")) == "file_patch_applied"):
        validation_errors.append("summary total_file_patches_applied does not match canonical file_patch_applied events.")
    if patch_failed_count != sum(1 for e in events if str(e.get("event_type", "")) == "file_patch_failed"):
        validation_errors.append("summary total_file_patch_failures does not match canonical file_patch_failed events.")

    if warnings:
        summary["aggregation_warnings"] = warnings
    if validation_errors:
        summary["validation_errors"] = validation_errors

    validate_summary(summary)
    return summary


def _build_artifact_manifest(run_dir: Path) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    optional_artifacts = {
        *{name for key, name in DEBUG_JSONL_FILES.items() if key != "events"},
        "tool_calls.jsonl",
    }
    for name in ["events.jsonl", *sorted(optional_artifacts)]:
        path = run_dir / name
        exists = path.exists()
        optional = name in optional_artifacts
        manifest[name] = {
            "path": str(path),
            "optional": optional,
            "exists": exists,
            "state": "present" if exists else ("absent_optional" if optional else "absent_required"),
        }
    return manifest


def validate_summary(summary: dict[str, Any]) -> None:
    validation_errors = list(summary.get("validation_errors") or [])
    total_tool_calls = int(summary.get("total_tool_calls", 0) or 0)
    tool_calls_by_name = summary.get("tool_calls_by_name") if isinstance(summary.get("tool_calls_by_name"), dict) else {}
    commands_executed = int(summary.get("commands_executed", 0) or 0)

    if total_tool_calls != sum(int(v) for v in tool_calls_by_name.values()):
        validation_errors.append("Invariant failed: total_tool_calls must equal sum(tool_calls_by_name.values()).")
    if commands_executed > total_tool_calls:
        validation_errors.append("Invariant failed: commands_executed must be <= total_tool_calls.")

    files_touched = int(summary.get("files_touched", 0) or 0)
    unique_files_read = int(summary.get("unique_files_read", 0) or 0)
    unique_files_written = int(summary.get("unique_files_written", 0) or 0)
    if files_touched < unique_files_read:
        validation_errors.append("Invariant failed: files_touched must be >= unique_files_read.")
    if files_touched < unique_files_written:
        validation_errors.append("Invariant failed: files_touched must be >= unique_files_written.")

    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    for name, info in artifacts.items():
        if not isinstance(info, dict):
            validation_errors.append(f"Artifact entry for {name} must be an object.")
            continue
        path = info.get("path")
        exists = bool(info.get("exists", False))
        if isinstance(path, str) and path:
            present = Path(path).exists()
            if present != exists:
                validation_errors.append(f"Artifact {name} existence flag mismatch.")
            if present is False and exists:
                validation_errors.append(f"Artifact {name} listed as present but file is missing.")
        optional = bool(info.get("optional", False))
        state = str(info.get("state", ""))
        if optional and not exists and state != "absent_optional":
            validation_errors.append(f"Artifact {name} must be marked absent_optional when missing.")
        if exists and state != "present":
            validation_errors.append(f"Artifact {name} must be marked present when file exists.")
        if not optional and not exists:
            validation_errors.append(f"Required artifact {name} is missing.")

    if validation_errors:
        raise ValueError("; ".join(validation_errors))


def write_summary_from_events(run_dir: Path, *, status_override: str | None = None) -> Path:
    write_tool_calls_from_events(run_dir)
    summary = aggregate_summary_from_events(run_dir, status_override=status_override)
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
