from __future__ import annotations

import atexit
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from villani_code.runtime_paths import get_debug_dir


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DebugRecorder:
    def __init__(self, repo: Path, enabled: bool = False, debug_dir: Path | None = None, level: str = "standard"):
        self.repo = repo.resolve()
        self.enabled = enabled
        self.level = level
        self._bundle_dir: Path | None = None
        self._session_meta: dict[str, Any] = {}
        self._finalized = False
        self._command_counter = 0
        self._debug_root_override = debug_dir
        if self.enabled:
            atexit.register(self.finalize)

    @property
    def bundle_dir(self) -> Path | None:
        return self._bundle_dir

    def start_run(self, session_id: str, objective: str, meta: dict[str, Any]) -> None:
        if not self.enabled:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        root = self._debug_root_override or get_debug_dir(self.repo)
        self._bundle_dir = root / f"{stamp}_{session_id}"
        self._bundle_dir.mkdir(parents=True, exist_ok=True)
        (self._bundle_dir / "command_outputs").mkdir(parents=True, exist_ok=True)
        self._session_meta = {
            "session_id": session_id,
            "start_timestamp": _utc_now(),
            "repo_path": str(self.repo),
            "objective": objective,
            **meta,
        }
        self._write_json("session_meta.json", self._session_meta)
        self._write_tree("workspace_tree_initial.txt")

    def record_beliefs(self, beliefs: dict[str, Any], phase: str, step_index: int | None = None) -> None:
        if not self._bundle_dir:
            return
        if phase == "initial":
            self._write_json("beliefs_initial.json", beliefs)
            return
        if phase == "final":
            self._write_json("beliefs_final.json", beliefs)
            return
        row = {"timestamp": _utc_now(), "step_index": step_index, "beliefs": beliefs}
        self._append_jsonl("beliefs_steps.jsonl", row)

    def record_action(self, row: dict[str, Any]) -> None:
        self._append_jsonl("actions.jsonl", {"timestamp": _utc_now(), **row})

    def record_event(self, event: dict[str, Any]) -> None:
        if not self._bundle_dir:
            return
        self._append_jsonl("events.jsonl", {"timestamp": _utc_now(), **event})
        etype = str(event.get("type", ""))
        if etype in {"tool_use", "tool_finished", "tool_result"}:
            self._append_jsonl("tool_calls.jsonl", {"timestamp": _utc_now(), **event})
        if etype.startswith("validation"):
            self._append_jsonl("validation_runs.jsonl", {"timestamp": _utc_now(), **event})
        if etype in {"edit_proposed", "tool_result"}:
            self._append_jsonl("file_changes.jsonl", {"timestamp": _utc_now(), **event})

    def record_command(self, step_id: str, command: str, exit_code: int, stdout: str, stderr: str = "", cwd: str = "") -> None:
        if not self._bundle_dir:
            return
        self._command_counter += 1
        base = f"{step_id}_{self._command_counter}"
        out_path = self._bundle_dir / "command_outputs" / f"{base}_stdout.txt"
        err_path = self._bundle_dir / "command_outputs" / f"{base}_stderr.txt"
        out_path.write_text(stdout or "", encoding="utf-8")
        err_path.write_text(stderr or "", encoding="utf-8")
        self._append_jsonl(
            "commands.jsonl",
            {
                "timestamp": _utc_now(),
                "step_id": step_id,
                "cwd": cwd,
                "command": command,
                "exit_code": exit_code,
                "stdout_path": str(out_path),
                "stderr_path": str(err_path),
            },
        )

    def finalize(self, *, exit_status: str = "unknown", stop_reason: str = "", confidence: float | None = None) -> None:
        if not self._bundle_dir or self._finalized:
            return
        self._finalized = True
        self._session_meta.update(
            {
                "end_timestamp": _utc_now(),
                "exit_status": exit_status,
                "stop_reason": stop_reason,
                "final_completion_confidence": confidence,
            }
        )
        self._write_json("session_meta.json", self._session_meta)
        self._write_tree("workspace_tree_final.txt")
        summary = (
            f"# Debug Summary\n\n"
            f"- Objective: {self._session_meta.get('objective', '')}\n"
            f"- Exit status: {exit_status}\n"
            f"- Stop reason: {stop_reason}\n"
            f"- Bundle: {self._bundle_dir}\n"
        )
        (self._bundle_dir / "debug_summary.md").write_text(summary, encoding="utf-8")

    def _write_tree(self, name: str) -> None:
        if not self._bundle_dir:
            return
        rows = []
        for path in sorted(self.repo.rglob("*")):
            rel = path.relative_to(self.repo).as_posix()
            if rel.startswith(".git/"):
                continue
            rows.append(rel)
            if len(rows) >= 5000:
                break
        (self._bundle_dir / name).write_text("\n".join(rows) + "\n", encoding="utf-8")

    def _write_json(self, name: str, payload: dict[str, Any]) -> None:
        if not self._bundle_dir:
            return
        (self._bundle_dir / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _append_jsonl(self, name: str, payload: dict[str, Any]) -> None:
        if not self._bundle_dir:
            return
        path = self._bundle_dir / name
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
