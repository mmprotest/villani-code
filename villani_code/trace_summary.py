from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGGREGATION_VERSION = "v1"
SHELL_TOOL_NAMES = {"bash", "shell", "ls"}


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
    return name.strip() or "unknown"


def _is_shell_tool(tool_name: str) -> bool:
    return tool_name.strip().lower() in SHELL_TOOL_NAMES


def aggregate_summary_from_events(run_dir: Path, *, status_override: str | None = None) -> dict[str, Any]:
    events_path = run_dir / "events.jsonl"
    events = load_events(events_path)
    warnings: list[str] = []

    started_at = None
    ended_at = None
    run_id = ""
    status = status_override

    tool_start_ids: set[str] = set()
    tool_call_name_by_id: dict[str, str] = {}
    tool_calls_by_name: Counter[str] = Counter()
    tool_failures_by_name: Counter[str] = Counter()
    tool_terminal_ids: set[str] = set()

    read_count = 0
    write_count = 0
    patch_applied_count = 0
    patch_failed_count = 0
    read_paths: set[str] = set()
    write_paths: set[str] = set()
    touched_paths: set[str] = set()

    model_requests = 0
    model_failures = 0
    tokens_input = 0
    tokens_output = 0

    turns: set[int] = set()

    shell_started_ids: set[str] = set()
    shell_failed_ids: set[str] = set()

    saw_legacy_tool_signal = False

    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

        maybe_run_id = event.get("run_id")
        if isinstance(maybe_run_id, str) and maybe_run_id:
            run_id = maybe_run_id

        turn_index = event.get("turn_index")
        if isinstance(turn_index, int):
            turns.add(turn_index)

        ts = _parse_ts(event.get("ts"))
        if ts is not None:
            if started_at is None or ts < started_at:
                started_at = ts
            if ended_at is None or ts > ended_at:
                ended_at = ts

        if event_type in {"run_completed", "run_failed"}:
            status = "completed" if event_type == "run_completed" else "failed"

        if event_type in {"tool_call", "tool_use", "tool_result", "tool_finished"}:
            saw_legacy_tool_signal = True

        if event_type == "tool_call_started":
            tool_name = _normalize_tool_name(payload.get("tool_name"))
            tool_call_id = str(payload.get("tool_call_id", "")).strip()
            if not tool_call_id:
                warnings.append("tool_call_started missing tool_call_id")
                continue
            if tool_call_id in tool_start_ids:
                warnings.append(f"duplicate tool_call_started for tool_call_id={tool_call_id}")
                continue
            tool_start_ids.add(tool_call_id)
            tool_call_name_by_id[tool_call_id] = tool_name
            tool_calls_by_name[tool_name] += 1
            if _is_shell_tool(tool_name):
                shell_started_ids.add(tool_call_id)
            continue

        if event_type == "tool_call_failed":
            tool_name = _normalize_tool_name(payload.get("tool_name"))
            tool_call_id = str(payload.get("tool_call_id", "")).strip()
            if tool_call_id:
                tool_terminal_ids.add(tool_call_id)
                if tool_call_id in shell_started_ids:
                    shell_failed_ids.add(tool_call_id)
            tool_failures_by_name[tool_name] += 1
            continue

        if event_type == "tool_call_completed":
            tool_call_id = str(payload.get("tool_call_id", "")).strip()
            tool_name = _normalize_tool_name(payload.get("tool_name"))
            if tool_call_id:
                tool_terminal_ids.add(tool_call_id)
                if _is_shell_tool(tool_name) or tool_call_id in shell_started_ids:
                    exit_code = payload.get("exit_code")
                    if isinstance(exit_code, int) and exit_code != 0:
                        shell_failed_ids.add(tool_call_id)
            continue

        if event_type == "file_read":
            read_count += 1
            path = str(payload.get("file_path", "")).strip()
            if path:
                read_paths.add(path)
                touched_paths.add(path)
            continue

        if event_type == "file_write":
            write_count += 1
            path = str(payload.get("file_path", "")).strip()
            if path:
                write_paths.add(path)
                touched_paths.add(path)
            continue

        if event_type == "file_patch_applied":
            patch_applied_count += 1
            path = str(payload.get("file_path", "")).strip()
            if path:
                write_paths.add(path)
                touched_paths.add(path)
            continue

        if event_type == "file_patch_failed":
            patch_failed_count += 1
            path = str(payload.get("file_path", "")).strip()
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
            in_tokens = payload.get("tokens_input")
            out_tokens = payload.get("tokens_output")
            if isinstance(in_tokens, int):
                tokens_input += in_tokens
            if isinstance(out_tokens, int):
                tokens_output += out_tokens
            continue

    total_tool_calls: int | None
    if tool_start_ids:
        total_tool_calls = len(tool_start_ids)
    elif saw_legacy_tool_signal:
        total_tool_calls = None
        warnings.append(
            "No canonical tool_call_started events found; refusing to report a potentially false tool count."
        )
    else:
        total_tool_calls = 0

    unterminated = sorted(tool_start_ids - tool_terminal_ids)
    if unterminated:
        warnings.append(f"Unterminated tool calls detected: {', '.join(unterminated[:10])}")

    commands_executed = len(shell_started_ids)
    commands_failed = len(shell_failed_ids)

    if status is None:
        status = "failed" if any(str(e.get("event_type", "")).strip() == "run_failed" for e in events) else "completed"

    started_at_iso = started_at.isoformat() if started_at is not None else None
    ended_at_iso = ended_at.isoformat() if ended_at is not None else None
    duration_ms: int | None = None
    if started_at is not None and ended_at is not None:
        duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))

    artifacts = _build_artifact_manifest(run_dir, warnings)

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
        "commands_executed": commands_executed,
        "commands_failed": commands_failed,
        "artifacts": artifacts,
        "aggregation_version": AGGREGATION_VERSION,
    }
    if warnings:
        summary["aggregation_warnings"] = warnings

    validate_summary(summary)
    return summary


def _build_artifact_manifest(run_dir: Path, warnings: list[str]) -> dict[str, dict[str, Any]]:
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
        optional = name in optional_artifacts
        if not exists and not optional:
            warnings.append(f"required artifact missing: {name}")
        manifest[name] = {
            "path": str(path),
            "optional": optional,
            "exists": exists,
        }
    return manifest


def validate_summary(summary: dict[str, Any]) -> None:
    total_tool_calls = summary.get("total_tool_calls")
    tool_calls_by_name = summary.get("tool_calls_by_name") or {}
    commands_executed = int(summary.get("commands_executed", 0) or 0)

    if total_tool_calls is not None:
        if total_tool_calls != sum(int(v) for v in tool_calls_by_name.values()):
            raise ValueError("Invariant failed: total_tool_calls must equal sum(tool_calls_by_name.values()).")
        if commands_executed > total_tool_calls:
            raise ValueError("Invariant failed: commands_executed must be <= total_tool_calls.")

    files_touched = int(summary.get("files_touched", 0) or 0)
    unique_files_read = int(summary.get("unique_files_read", 0) or 0)
    unique_files_written = int(summary.get("unique_files_written", 0) or 0)
    if files_touched < unique_files_read:
        raise ValueError("Invariant failed: files_touched must be >= unique_files_read.")
    if files_touched < unique_files_written:
        raise ValueError("Invariant failed: files_touched must be >= unique_files_written.")

    bash_count = int(tool_calls_by_name.get("Bash", 0) or 0)
    if bash_count > 0 and total_tool_calls == 0:
        raise ValueError("Invariant failed: Bash calls exist but total_tool_calls is zero.")

    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    for name, info in artifacts.items():
        if not isinstance(info, dict):
            raise ValueError(f"Artifact entry for {name} must be an object.")
        path = info.get("path")
        optional = bool(info.get("optional", False))
        exists = bool(info.get("exists", False))
        if path and isinstance(path, str):
            if not Path(path).exists() and not optional:
                raise ValueError(f"Artifact {name} does not exist at path={path}.")
            if Path(path).exists() != exists:
                raise ValueError(f"Artifact {name} existence flag mismatch.")


def write_summary_from_events(run_dir: Path, *, status_override: str | None = None) -> Path:
    summary = aggregate_summary_from_events(run_dir, status_override=status_override)
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
