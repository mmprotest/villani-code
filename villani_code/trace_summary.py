from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGGREGATION_VERSION = "v2"
TOOL_CALL_SCHEMA_VERSION = "v1"
SHELL_TOOL_NAMES = {"bash", "shell", "sh", "zsh", "ls"}


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
        row: dict[str, Any] = {
            "event_id": self._next_event_id,
            "run_id": self.run_id,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
            "turn_index": turn_index,
            "event_type": event_type,
            "payload": payload,
        }
        self._next_event_id += 1
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row


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
    records: list[dict[str, Any]] = []

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

        if event_type not in {"tool_call_completed", "tool_call_failed"}:
            continue

        tool_call_id = str(payload.get("tool_call_id", "")).strip()
        if not tool_call_id:
            validation_errors.append(f"{event_type} missing tool_call_id")
            continue
        start = started.pop(tool_call_id, None)
        if start is None:
            warnings.append(f"terminal tool event without matching start: {tool_call_id}")
            start = {
                "tool_call_id": tool_call_id,
                "run_id": str(event.get("run_id") or run_dir.name),
                "turn_index": turn_index,
                "tool_name": _normalize_tool_name(payload.get("tool_name")),
                "tool_category": _infer_tool_category(_normalize_tool_name(payload.get("tool_name"))),
                "started_at": event.get("ts"),
                "args": {},
            }

        tool_name = _normalize_tool_name(payload.get("tool_name") or start.get("tool_name"))
        status = "failed" if event_type == "tool_call_failed" else "success"
        started_at_dt = _parse_ts(start.get("started_at"))
        ended_at_dt = _parse_ts(event.get("ts"))
        duration_ms: int | None = None
        if started_at_dt is not None and ended_at_dt is not None:
            duration_ms = max(0, int((ended_at_dt - started_at_dt).total_seconds() * 1000))

        args = start.get("args") if isinstance(start.get("args"), dict) else {}
        normalized_args_summary = {
            "file_path": normalize_repo_path(args.get("file_path"), repo_root) if "file_path" in args else None,
            "command": _truncate(args.get("command"), 160) if "command" in args else None,
            "cwd": normalize_repo_path(args.get("cwd"), repo_root) if "cwd" in args else None,
            "arg_keys": sorted(args.keys()),
        }

        result_summary = payload.get("result_summary") if isinstance(payload.get("result_summary"), dict) else {}
        if tool_name.lower() == "bash":
            result_summary = {
                "command": _truncate(args.get("command"), 160),
                "cwd": normalize_repo_path(args.get("cwd", "."), repo_root),
                "exit_code": payload.get("exit_code"),
                "summary": _truncate(payload.get("summary"), 240),
            }
        elif not result_summary:
            result_summary = {"summary": _truncate(payload.get("summary"), 240)}

        error_value = None
        if status == "failed":
            error_value = {
                "error_type": payload.get("error_type") or "tool_error",
                "message": _truncate(payload.get("summary"), 240),
            }

        records.append(
            {
                "tool_call_id": tool_call_id,
                "run_id": start.get("run_id"),
                "turn_index": start.get("turn_index"),
                "tool_name": tool_name,
                "tool_category": _infer_tool_category(tool_name),
                "started_at": start.get("started_at"),
                "ended_at": event.get("ts"),
                "duration_ms": duration_ms,
                "status": status,
                "args": args,
                "normalized_args_summary": normalized_args_summary,
                "result_summary": result_summary,
                "error": error_value,
                "schema_version": TOOL_CALL_SCHEMA_VERSION,
            }
        )

    if started:
        warnings.append(
            "Unterminated tool calls detected: " + ", ".join(sorted(started.keys())[:10])
        )

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
    model_failures = 0
    tokens_input_total = 0
    tokens_output_total = 0
    saw_tokens_input = False
    saw_tokens_output = False

    turns: set[int] = set()
    saw_turn_events = False

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
            status = "completed" if event_type == "run_completed" else "failed"
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
            path = normalize_repo_path(payload.get("file_path"), repo_root)
            if path:
                touched_paths.add(path)
            continue

        if event_type == "model_request_started":
            model_requests += 1
            continue

        if event_type == "model_request_failed":
            model_failures += 1
            continue

        if event_type == "model_request_completed":
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
        if str(e.get("event_type", "")) == "command_finished"
    }
    command_tool_ids.discard("")
    tool_ids = {str(row.get("tool_call_id", "")).strip() for row in tool_rows}
    missing_tool_ids = sorted(command_tool_ids - tool_ids)
    if missing_tool_ids:
        validation_errors.append(
            "commands.jsonl/canonical command events contain tool_call_id values without matching tool-call records: "
            + ", ".join(missing_tool_ids[:10])
        )

    if warnings:
        summary["aggregation_warnings"] = warnings
    if validation_errors:
        summary["validation_errors"] = validation_errors

    validate_summary(summary)
    return summary


def _build_artifact_manifest(run_dir: Path) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    optional_artifacts = {
        "tool_calls.jsonl",
        "approvals.jsonl",
        "validations.jsonl",
        "commands.jsonl",
        "patches.jsonl",
        "turns.jsonl",
        "model_requests.jsonl",
        "model_responses.jsonl",
        "mission_state_snapshots.jsonl",
    }
    for name in ["events.jsonl", *sorted(optional_artifacts)]:
        path = run_dir / name
        exists = path.exists()
        manifest[name] = {
            "path": str(path),
            "optional": name in optional_artifacts,
            "exists": exists,
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

    if validation_errors:
        raise ValueError("; ".join(validation_errors))


def write_summary_from_events(run_dir: Path, *, status_override: str | None = None) -> Path:
    write_tool_calls_from_events(run_dir)
    summary = aggregate_summary_from_events(run_dir, status_override=status_override)
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
