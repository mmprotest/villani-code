from __future__ import annotations

import copy
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from villani_code.debug_artifacts import DEBUG_JSONL_FILES, append_jsonl, append_text, create_debug_run_artifacts, write_json
from villani_code.debug_mode import DebugConfig
from villani_code.trace_summary import (
    EventLogger,
    normalize_repo_path,
    normalize_token_usage,
    write_summary_from_events,
    write_tool_calls_from_events,
)

_RESULT_PREVIEW_LIMIT = 240


class DebugRecorder:
    def __init__(self, config: DebugConfig, run_id: str, objective: str, repo: Path, mode: str, model: str, provider: str | None = None):
        self.config = config
        self.run_id = run_id
        self._seq = 0
        self._objective = objective
        self._repo = str(repo)
        self._runtime_mode = mode
        self._model = model
        self._provider = provider
        self._changed_files: set[str] = set()
        self._last_failed_command: str = ""
        self._last_failed_validation: str = ""
        self.artifacts = create_debug_run_artifacts(run_id=run_id, debug_root=config.debug_root)
        self._jsonl_paths = {k: self.artifacts.path(v) for k, v in DEBUG_JSONL_FILES.items()}
        self._event_logger = EventLogger(run_id=run_id, events_path=self._jsonl_paths["events"])
        self._tool_call_to_name: dict[str, str] = {}
        self._current_turn_index: int | None = None
        self._model_request_seq = 0
        self._pending_model_request_ids: list[str] = []
        self._safe_write_json(
            self.artifacts.path("session_meta.json"),
            {
                "run_id": run_id,
                "objective": objective,
                "repo": self._repo,
                "debug_mode": config.mode.value,
                "runtime_mode": mode,
                "model": model,
                "provider": provider,
                "created_at": self._ts(),
            },
        )
        self._emit("run_started", {"objective": objective, "runtime_mode": mode, "model": model, "provider": provider})

    def _normalize_changed_path(self, file_path: str) -> str:
        return normalize_repo_path(file_path, Path(self._repo))

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _safe(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            try:
                append_text(self.artifacts.path("stderr.log"), traceback.format_exc() + "\n")
            except Exception:
                return None
            return None

    def _safe_append_jsonl(self, key: str, payload: dict[str, Any]) -> None:
        path = self._jsonl_paths.get(key)
        if path is None:
            return
        self._safe(append_jsonl, path, payload)

    def _safe_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._safe(write_json, path, payload)

    def _emit(self, event_type: str, payload: dict[str, Any], turn_index: int | None = None) -> None:
        resolved_turn = self._current_turn_index if turn_index is None else turn_index
        self._safe(self._event_logger.emit, event_type, payload, resolved_turn)

    def record_event(
        self,
        event_type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        phase: str = "execution",
        turn_index: int | None = None,
    ) -> None:
        self._seq += 1
        # Preserve legacy compatibility by carrying generic events into the canonical stream.
        self._emit(event_type, {"summary": summary, "phase": phase, **(payload or {})}, turn_index=turn_index)

    def record_turn_start(self, turn_index: int, payload: dict[str, Any]) -> None:
        self._current_turn_index = turn_index
        row = {"ts": self._ts(), "turn_index": turn_index, "payload": payload}
        self._safe_append_jsonl("turns", row)
        self.record_event("turn_started", f"Turn {turn_index} started", payload, turn_index=turn_index)

    def record_turn_finish(self, turn_index: int, stop_reason: str = "") -> None:
        self.record_event("turn_finished", f"Turn {turn_index} finished", {"turn_index": turn_index, "stop_reason": stop_reason}, turn_index=turn_index)
        self._current_turn_index = None

    def record_model_request(self, payload: dict[str, Any]) -> None:
        data = payload if self.config.capture_model_io else {"model": payload.get("model"), "message_count": len(payload.get("messages", []))}
        self._model_request_seq += 1
        request_id = f"mr-{self._model_request_seq}"
        self._pending_model_request_ids.append(request_id)
        self._safe_append_jsonl("model_requests", {"ts": self._ts(), "payload": data})
        self._emit(
            "model_request_started",
            {"request_id": request_id, "model": payload.get("model"), "message_count": len(payload.get("messages", []))},
        )

    def record_model_response(self, payload: dict[str, Any]) -> None:
        data = payload if self.config.capture_model_io else {"stop_reason": payload.get("stop_reason"), "content_blocks": len(payload.get("content", []))}
        self._safe_append_jsonl("model_responses", {"ts": self._ts(), "payload": data})
        request_id = self._pending_model_request_ids.pop(0) if self._pending_model_request_ids else ""
        usage = normalize_token_usage(payload)
        self._emit(
            "model_request_completed",
            {
                "request_id": request_id,
                "stop_reason": payload.get("stop_reason"),
                "tokens_input": usage.get("tokens_input"),
                "tokens_output": usage.get("tokens_output"),
                "tokens_total": usage.get("tokens_total"),
            },
        )

    def record_model_request_failed(self, error: str) -> None:
        request_id = self._pending_model_request_ids.pop(0) if self._pending_model_request_ids else ""
        self._emit(
            "model_request_failed",
            {
                "request_id": request_id,
                "error_type": "model_request_error",
                "error": {"message": str(error)},
            },
        )

    def record_tool_call(self, name: str, args: dict[str, Any], tool_use_id: str = "", turn_index: int | None = None) -> None:
        data = args if self.config.capture_full_tool_payloads else {k: args[k] for k in ("file_path", "command") if k in args}
        tool_call_id = tool_use_id or f"tool-{self._seq + 1}"
        self._tool_call_to_name[tool_call_id] = name
        self._emit(
            "tool_call_started",
            {
                "tool_name": name,
                "tool_call_id": tool_call_id,
                "args": data,
            },
            turn_index=turn_index,
        )

    def record_tool_result(
        self,
        name: str,
        is_error: bool,
        summary: str = "",
        tool_use_id: str = "",
        exit_code: int | None = None,
        result_payload: dict[str, Any] | None = None,
        turn_index: int | None = None,
    ) -> None:
        tool_call_id = tool_use_id or ""
        if not tool_call_id:
            for known_id, known_name in reversed(list(self._tool_call_to_name.items())):
                if known_name == name:
                    tool_call_id = known_id
                    break
        payload_data = result_payload if isinstance(result_payload, dict) else {}
        result_summary = self._normalize_result_summary(name, payload_data, summary)
        payload = {
            "tool_name": name,
            "tool_call_id": tool_call_id,
            "summary": summary,
            "status": "failed" if is_error else "completed",
            "result_summary": result_summary,
            "result": payload_data,
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code
        if is_error:
            payload["error_type"] = "tool_error"
            payload["error"] = payload_data.get("error") if isinstance(payload_data.get("error"), dict) else {
                "message": str(payload_data.get("content", summary or "")),
            }
            self._emit("tool_call_failed", payload, turn_index=turn_index)
        else:
            self._emit("tool_call_completed", payload, turn_index=turn_index)

    def _normalize_result_summary(self, name: str, payload_data: dict[str, Any], summary: str) -> dict[str, Any]:
        lowered = name.lower()
        if lowered == "bash":
            stdout = str(payload_data.get("stdout", ""))
            stderr = str(payload_data.get("stderr", ""))
            return {
                "kind": "command_result",
                "command": payload_data.get("command"),
                "exit_code": payload_data.get("exit_code"),
                "stdout_preview": stdout[:_RESULT_PREVIEW_LIMIT],
                "stderr_preview": stderr[:_RESULT_PREVIEW_LIMIT],
                "stdout_truncated": bool(payload_data.get("stdout_truncated", len(stdout) > _RESULT_PREVIEW_LIMIT)),
                "stderr_truncated": bool(payload_data.get("stderr_truncated", len(stderr) > _RESULT_PREVIEW_LIMIT)),
            }
        if lowered == "read":
            return {
                "kind": "file_read_result",
                "path": payload_data.get("file_path") or payload_data.get("path"),
                "bytes_read": payload_data.get("size_bytes") or payload_data.get("bytes_read"),
                "lines_read": payload_data.get("lines_read"),
                "preview": str(payload_data.get("preview", ""))[:_RESULT_PREVIEW_LIMIT] if payload_data.get("preview") is not None else None,
            }
        if lowered == "write":
            return {
                "kind": "file_write_result",
                "path": payload_data.get("file_path") or payload_data.get("path"),
                "bytes_written": payload_data.get("size_bytes") or payload_data.get("bytes_written"),
                "lines_written": payload_data.get("lines_written"),
                "created": payload_data.get("created"),
                "overwrote": payload_data.get("overwrote"),
            }
        if lowered == "patch":
            return {
                "kind": "file_patch_result",
                "path": payload_data.get("file_path") or payload_data.get("path"),
                "ok": not bool(payload_data.get("is_error", False)),
                "bytes_delta": payload_data.get("bytes_delta"),
                "lines_added": payload_data.get("lines_added"),
                "lines_removed": payload_data.get("lines_removed"),
                "failure_reason": payload_data.get("failure_reason"),
            }
        return {
            "kind": "tool_result",
            "summary": str(summary or payload_data.get("content", ""))[:_RESULT_PREVIEW_LIMIT],
        }

    def record_command_start(self, command: str, cwd: str, tool_call_id: str = "", turn_index: int | None = None) -> None:
        self.record_event(
            "command_started",
            f"Command started: {command}",
            {"command": command, "cwd": cwd, "tool_call_id": tool_call_id},
            turn_index=turn_index,
        )

    def record_command_finish(
        self,
        command: str,
        cwd: str,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
        truncated: bool = False,
        tool_call_id: str = "",
        turn_index: int | None = None,
    ) -> None:
        payload = {
            "ts": self._ts(),
            "command": command,
            "cwd": cwd,
            "exit_code": exit_code,
            "stdout": stdout if self.config.capture_command_output else stdout[:240],
            "stderr": stderr if self.config.capture_command_output else stderr[:240],
            "truncated": truncated,
            "tool_call_id": tool_call_id,
        }
        self._safe_append_jsonl("commands", payload)
        self.record_event("command_finished", f"Command finished: {command}", payload, turn_index=turn_index)
        if exit_code != 0:
            self._last_failed_command = command

    def record_file_read(
        self,
        file_path: str,
        size_bytes: int,
        ok: bool = True,
        tool_call_id: str | None = None,
        turn_index: int | None = None,
    ) -> None:
        self._emit(
            "file_read",
            {"file_path": file_path, "size_bytes": size_bytes, "ok": ok, "tool_call_id": tool_call_id or ""},
            turn_index=turn_index,
        )

    def record_file_write(
        self,
        file_path: str,
        size_bytes: int,
        ok: bool = True,
        tool_call_id: str | None = None,
        turn_index: int | None = None,
    ) -> None:
        normalized_path = self._normalize_changed_path(file_path)
        if normalized_path:
            self._changed_files.add(normalized_path)
        self._emit(
            "file_write",
            {"file_path": file_path, "size_bytes": size_bytes, "ok": ok, "tool_call_id": tool_call_id or ""},
            turn_index=turn_index,
        )

    def record_patch_applied(
        self,
        file_path: str,
        ok: bool = True,
        tool_call_id: str | None = None,
        failure_reason: str = "",
        hunks_attempted: Any | None = None,
        hunks_failed: Any | None = None,
        turn_index: int | None = None,
    ) -> None:
        normalized_path = self._normalize_changed_path(file_path)
        if normalized_path:
            self._changed_files.add(normalized_path)
        self._safe_append_jsonl("patches", {"ts": self._ts(), "file_path": file_path, "ok": ok})
        payload = {"file_path": file_path, "ok": ok, "tool_call_id": tool_call_id or ""}
        if failure_reason:
            payload["failure_reason"] = failure_reason
        if isinstance(hunks_attempted, int):
            payload["hunks_attempted"] = hunks_attempted
        if isinstance(hunks_failed, int):
            payload["hunks_failed"] = hunks_failed
        self._emit(
            "file_patch_applied" if ok else "file_patch_failed",
            payload,
            turn_index=turn_index,
        )

    def record_approval_requested(self, tool_name: str, payload: dict[str, Any], turn_index: int | None = None) -> None:
        row = {"ts": self._ts(), "tool_name": tool_name, "payload": payload}
        self._safe_append_jsonl("approvals", {**row, "state": "requested"})
        self.record_event("approval_requested", f"Approval requested for {tool_name}", row, turn_index=turn_index)

    def record_approval_resolved(self, tool_name: str, approved: bool, payload: dict[str, Any], turn_index: int | None = None) -> None:
        row = {"ts": self._ts(), "tool_name": tool_name, "approved": approved, "payload": payload}
        self._safe_append_jsonl("approvals", {**row, "state": "resolved"})
        self.record_event("approval_resolved", f"Approval resolved for {tool_name}", row, turn_index=turn_index)

    def record_validation_start(self, kind: str, payload: dict[str, Any]) -> None:
        self._safe_append_jsonl("validations", {"ts": self._ts(), "state": "started", "kind": kind, "payload": payload})
        self.record_event("validation_started", f"Validation started: {kind}", payload)

    def record_validation_finish(self, kind: str, exit_code: int, summary: str) -> None:
        row = {"ts": self._ts(), "state": "finished", "kind": kind, "exit_code": exit_code, "summary": summary}
        self._safe_append_jsonl("validations", row)
        self.record_event("validation_finished", f"Validation finished: {kind}", row)
        if exit_code != 0:
            self._last_failed_validation = summary or kind

    def record_context_compacted(self, payload: dict[str, Any]) -> None:
        self.record_event("context_compacted", "Context compacted", payload)

    def record_mission_state_snapshot(self, mission_state: dict[str, Any], reason: str, turn_index: int | None = None) -> None:
        if not self.config.capture_mission_snapshots:
            return
        row = {"ts": self._ts(), "reason": reason, "mission_state": copy.deepcopy(mission_state)}
        self._safe_append_jsonl("mission_state_snapshots", row)
        self.record_event("mission_state_updated", f"Mission state updated: {reason}", {"reason": reason}, turn_index=turn_index)

    def record_subagent_start(self, objective: str, payload: dict[str, Any] | None = None) -> None:
        self.record_event("subagent_started", objective, payload or {})

    def record_subagent_finish(self, objective: str, payload: dict[str, Any] | None = None) -> None:
        self.record_event("subagent_finished", objective, payload or {})

    def record_error(self, summary: str, payload: dict[str, Any] | None = None) -> None:
        body = payload or {}
        self._emit("run_failed", {"summary": summary, **body})
        self.record_event("error", summary, body)

    def write_prompt_rendered(self, text: str) -> None:
        self._safe(append_text, self.artifacts.path("prompt_rendered.txt"), text)

    def write_working_context(self, text: str) -> None:
        self._safe(append_text, self.artifacts.path("working_context.txt"), text)

    def write_final_summary(self, *, status: str, termination_reason: str, total_turns: int, mission_id: str = "") -> Path:
        if status == "completed":
            self._emit("run_completed", {"termination_reason": termination_reason, "mission_id": mission_id, "total_turns": total_turns})
        elif status == "failed":
            self._emit("run_failed", {"termination_reason": termination_reason, "mission_id": mission_id, "total_turns": total_turns})

        self._safe(write_tool_calls_from_events, self.artifacts.run_dir)
        summary_path = self._safe(write_summary_from_events, self.artifacts.run_dir, status_override=status)
        if isinstance(summary_path, Path):
            self._emit("summary_generated", {"summary_path": str(summary_path)})
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            summary_payload["total_turns"] = total_turns
            summary_payload["termination_reason"] = termination_reason
            summary_payload["mission_id"] = mission_id
            summary_payload["changed_files"] = sorted(self._changed_files)
            summary_payload["last_failed_command"] = self._last_failed_command
            summary_payload["last_failed_validation"] = self._last_failed_validation
            final_path = self.artifacts.path("final_summary.json")
            self._safe(write_json, final_path, summary_payload)
            return final_path

        fallback = {
            "run_id": self.run_id,
            "status": status,
            "termination_reason": termination_reason,
            "total_turns": total_turns,
            "mission_id": mission_id,
            "error": "failed to generate summary from events",
        }
        path = self.artifacts.path("final_summary.json")
        self._safe_write_json(path, fallback)
        return path

    def on_runner_event(self, event: dict[str, Any]) -> None:
        etype = str(event.get("type", ""))
        if not etype:
            return
        event_turn_index = event.get("turn_index") if isinstance(event.get("turn_index"), int) else None
        if etype == "tool_started":
            self.record_tool_call(
                str(event.get("name", "")),
                dict(event.get("input", {})),
                str(event.get("tool_use_id", event.get("tool_call_id", ""))),
                turn_index=event_turn_index,
            )
            return
        if etype == "tool_result":
            result_payload = dict(event.get("result", {})) if isinstance(event.get("result"), dict) else {}
            if not result_payload:
                result_payload = {k: v for k, v in event.items() if k not in {"type", "name", "input", "tool_use_id", "tool_call_id", "turn_index"}}
            summary = str(result_payload.get("summary") or result_payload.get("content") or event.get("summary") or "")
            exit_code = result_payload.get("exit_code")
            self.record_tool_result(
                str(event.get("name", "")),
                bool(event.get("is_error", result_payload.get("is_error", False))),
                summary,
                str(event.get("tool_use_id", event.get("tool_call_id", ""))),
                int(exit_code) if isinstance(exit_code, int) else None,
                result_payload=result_payload,
                turn_index=event_turn_index,
            )
            return
        if etype == "tool_finished":
            return
        if etype in {"model_request_started", "model_request_completed", "model_request_failed"}:
            return
        if etype == "approval_required":
            self.record_approval_requested(str(event.get("name", "")), dict(event.get("input", {})), turn_index=event_turn_index)
            return
        if etype in {"approval_resolved", "approval_auto_resolved"}:
            self.record_approval_resolved(
                str(event.get("name", "")),
                bool(event.get("approved", True)),
                dict(event.get("input", {})),
                turn_index=event_turn_index,
            )
            return
        if etype == "validation_started":
            self.record_validation_start("post_execution", event)
            return
        if etype == "validation_completed":
            status = str(event.get("status", ""))
            code = 0 if status == "passed" else 1
            self.record_validation_finish("post_execution", code, status)
            return
        if etype == "context_compacted":
            self.record_context_compacted(event)
            return
        self.record_event(etype, etype, event, turn_index=event_turn_index)
