from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
import hashlib
import inspect
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from villani_code.execution import ExecutionBudget, VILLANI_TASK_BUDGET
from villani_code.integrations.pi_bridge_protocol import (
    RunCommand,
    parse_approval_response_command,
    parse_json_line,
    parse_run_command,
    ready_event,
    to_json_line,
)

RunnerFactory = Callable[..., Any]


@dataclass(slots=True)
class PendingApproval:
    run_id: str
    request_id: str
    tool: str
    ready: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None


@dataclass(slots=True)
class ActiveRun:
    command: RunCommand
    abort_requested: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    pending_approvals: dict[str, PendingApproval] = field(default_factory=dict)


class PiBridge:
    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.runner_factory = runner_factory or build_default_runner
        self._events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._active: dict[str, ActiveRun] = {}
        self._pending_approvals: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def emit(self, event: dict[str, Any]) -> None:
        self.stdout.write(to_json_line(event))
        self.stdout.flush()

    def run_stdio(self) -> None:
        self.emit(ready_event())
        commands: queue.Queue[str | None] = queue.Queue()

        def read_stdin() -> None:
            try:
                for raw_line in self.stdin:
                    commands.put(raw_line)
            finally:
                commands.put(None)

        reader = threading.Thread(target=read_stdin, daemon=True)
        reader.start()
        stdin_closed = False
        while True:
            self._drain_events()
            try:
                raw_line = commands.get(timeout=0.05)
            except queue.Empty:
                if stdin_closed and not self._active:
                    break
                continue
            if raw_line is None:
                stdin_closed = True
                if not self._active:
                    break
                continue
            line = raw_line.strip()
            if not line:
                continue
            try:
                command = parse_json_line(line)
                self._handle_command(command)
            except Exception as exc:  # noqa: BLE001
                self.emit({"type": "error", "error": str(exc)})
        self._drain_events()

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            if event is not None:
                self.emit(event)

    def _handle_command(self, command: dict[str, Any]) -> None:
        command_type = str(command.get("type") or "")
        if command_type == "ping":
            self.emit({"type": "pong", "id": command.get("id")})
            return
        if command_type == "run":
            self._start_run(parse_run_command(command))
            return
        if command_type == "abort":
            run_id = str(command.get("id") or "")
            self._abort_run(run_id)
            return
        if command_type == "approval_response":
            self._handle_approval_response(parse_approval_response_command(command))
            self._drain_events()
            return
        raise ValueError(f"Unknown bridge command type: {command_type or '<missing>'}")

    def _start_run(self, command: RunCommand) -> None:
        with self._lock:
            if command.id in self._active:
                raise ValueError(f"Run already active: {command.id}")
            active = ActiveRun(command=command)
            self._active[command.id] = active
        thread = threading.Thread(target=self._run_worker, args=(active,), daemon=True)
        active.thread = thread
        thread.start()

    def _abort_run(self, run_id: str) -> None:
        with self._lock:
            active = self._active.get(run_id)
        if active is None:
            self.emit({"type": "error", "id": run_id, "error": "No active run with that id"})
            return
        active.abort_requested.set()
        self._deny_pending_approvals(active)
        # Best-effort only: the existing Runner has no general cooperative cancellation hook yet.
        self.emit({"type": "abort_requested", "id": run_id})

    def _handle_approval_response(self, response: Any) -> None:
        with self._lock:
            pending = self._pending_approvals.get(response.request_id)
            active = self._active.get(response.id)
            if pending is None or active is None or pending.run_id != response.id:
                self.emit({"type": "error", "id": response.id, "error": f"Unknown approval request: {response.request_id}"})
                return
            # Remove first so duplicate responses cannot change the decision.
            self._pending_approvals.pop(response.request_id, None)
            active.pending_approvals.pop(response.request_id, None)
            pending.approved = bool(response.approved)
            pending.ready.set()
        self._events.put(
            {
                "type": "approval_resolved",
                "id": response.id,
                "request_id": response.request_id,
                "tool": pending.tool,
                "approved": bool(response.approved),
            }
        )

    def _deny_pending_approvals(self, active: ActiveRun) -> None:
        with self._lock:
            pending_items = list(active.pending_approvals.values())
            for pending in pending_items:
                self._pending_approvals.pop(pending.request_id, None)
                active.pending_approvals.pop(pending.request_id, None)
                pending.approved = False
                pending.ready.set()

    def _run_worker(self, active: ActiveRun) -> None:
        command = active.command
        repo = str(Path(command.repo).resolve())
        repo_path = Path(repo)
        before_dirty = git_changed_files(repo_path)
        before_dirty_hashes = hash_files(repo_path, before_dirty)
        touched_files: set[str] = set()
        transcript_path: str | None = None
        verification_passed: bool | None = None
        latest_summary = ""
        try:
            self._events.put(
                {
                    "type": "run_started",
                    "id": command.id,
                    "run_id": command.id,
                    "task": command.task,
                    "repo": repo,
                    "mode": command.mode,
                }
            )

            def on_runner_event(event: dict[str, Any]) -> None:
                nonlocal verification_passed, latest_summary
                for mapped in map_runner_event(command.id, event):
                    if mapped.get("type") == "verification_finished":
                        verification_passed = bool(mapped.get("passed"))
                    if mapped.get("type") == "workspace_changed":
                        for changed_path in mapped.get("files", []):
                            touched_files.add(str(changed_path).replace("\\", "/"))
                    self._events.put(mapped)
                if event.get("type") == "validation_completed":
                    verification_passed = bool(event.get("passed"))
                if event.get("type") in {"tool_result", "tool_finished"}:
                    latest_summary = summarize_tool_event(event) or latest_summary

            approval_callback = self._approval_callback(active)
            runner = self._create_runner(command, on_runner_event, approval_callback)
            if active.abort_requested.is_set():
                self._events.put({"type": "run_aborted", "id": command.id, "success": False, "summary": "Aborted by caller"})
                return
            result = run_existing_runner(runner, command)
            transcript_path = normalize_transcript_path(result)
            changed_files, preexisting_dirty_files = attributed_changed_files(repo_path, before_dirty, before_dirty_hashes, touched_files)
            if active.abort_requested.is_set():
                self._events.put(
                    {
                        "type": "run_aborted",
                        "id": command.id,
                        "success": False,
                        "summary": "Aborted by caller after runner stopped",
                        "changed_files": changed_files,
                        "preexisting_dirty_files": preexisting_dirty_files,
                        "transcript_path": transcript_path,
                    }
                )
                return
            summary = extract_summary(result) or latest_summary or "Villani run completed."
            self._events.put(
                {
                    "type": "run_completed",
                    "id": command.id,
                    "success": True,
                    "changed_files": changed_files,
                    "preexisting_dirty_files": preexisting_dirty_files,
                    "verification_passed": verification_passed,
                    "summary": summary,
                    "transcript_path": transcript_path,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self._events.put(
                {
                    "type": "run_failed",
                    "id": command.id,
                    "success": False,
                    "error": str(exc),
                    "summary": "Villani bridge run failed.",
                    "changed_files": attributed_changed_files(Path(repo), before_dirty, before_dirty_hashes, touched_files)[0],
                    "preexisting_dirty_files": before_dirty,
                    "transcript_path": transcript_path,
                }
            )
            self.stderr.write(traceback.format_exc())
            self.stderr.flush()
        finally:
            self._deny_pending_approvals(active)
            with self._lock:
                self._active.pop(command.id, None)

    def _create_runner(
        self,
        command: RunCommand,
        event_callback: Callable[[dict[str, Any]], None],
        approval_callback: Callable[[str, dict[str, Any]], bool],
    ) -> Any:
        try:
            signature = inspect.signature(self.runner_factory)
            parameters = list(signature.parameters.values())
            accepts_varargs = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters)
            positional = [
                parameter
                for parameter in parameters
                if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
            ]
            if accepts_varargs or len(positional) >= 3:
                return self.runner_factory(command, event_callback, approval_callback)
        except (TypeError, ValueError):
            # Some callables do not expose an inspectable signature; fall back to the legacy two-argument shape.
            pass
        runner = self.runner_factory(command, event_callback)
        runner.approval_callback = approval_callback
        return runner

    def _approval_callback(self, active: ActiveRun) -> Callable[[str, dict[str, Any]], bool]:
        def approve(tool_name: str, tool_input: dict[str, Any]) -> bool:
            if active.abort_requested.is_set():
                return False
            request_id = uuid.uuid4().hex
            summary, safe_input = summarize_approval_request(tool_name, tool_input)
            pending = PendingApproval(run_id=active.command.id, request_id=request_id, tool=str(tool_name))
            with self._lock:
                if active.abort_requested.is_set() or active.command.id not in self._active:
                    return False
                active.pending_approvals[request_id] = pending
                self._pending_approvals[request_id] = pending
            self._events.put(
                {
                    "type": "approval_required",
                    "id": active.command.id,
                    "request_id": request_id,
                    "tool": str(tool_name),
                    "summary": summary,
                    "input": safe_input,
                }
            )
            while not pending.ready.wait(timeout=0.05):
                if active.abort_requested.is_set():
                    with self._lock:
                        self._pending_approvals.pop(request_id, None)
                        active.pending_approvals.pop(request_id, None)
                    return False
            return pending.approved is True and not active.abort_requested.is_set()

        return approve


def summarize_approval_request(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    tool = str(tool_name or "tool")
    safe_input: dict[str, Any] = {}
    path = tool_input.get("path") or tool_input.get("file_path")
    command = tool_input.get("command")
    if tool in {"Write", "Patch"}:
        if path is not None:
            safe_input["path"] = cap_text(str(path).replace("\\", "/"), 500)
        content = tool_input.get("content")
        if isinstance(content, str):
            safe_input["content_chars"] = len(content)
        action = "Write file" if tool == "Write" else "Apply patch to"
        target = safe_input.get("path") or "unknown path"
        return f"{action}: {target}", safe_input
    if tool == "Bash":
        safe_input["command"] = cap_text(str(command or ""), 2000)
        return f"Run command: {safe_input['command']}", safe_input
    for key in ("path", "file_path", "command"):
        if key in tool_input and tool_input[key] is not None:
            safe_input[key if key != "file_path" else "path"] = cap_text(str(tool_input[key]), 1000)
    return f"Approve {tool} operation", safe_input


def cap_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def build_default_runner(command: RunCommand, event_callback: Callable[[dict[str, Any]], None], approval_callback: Callable[[str, dict[str, Any]], bool]) -> Any:
    from villani_code.cli import _build_runner

    provider = command.config.provider or os.environ.get("VILLANI_PROVIDER") or "anthropic"
    if provider not in {"anthropic", "openai"}:
        raise ValueError("provider must be 'anthropic' or 'openai'")
    model = command.config.model or os.environ.get("VILLANI_MODEL")
    base_url = command.config.base_url or os.environ.get("VILLANI_BASE_URL")
    if not model or not base_url:
        raise ValueError("run config requires model and base_url, or VILLANI_MODEL and VILLANI_BASE_URL")
    runner = _build_runner(
        base_url=base_url,
        model=model,
        repo=Path(command.repo),
        max_tokens=4096,
        stream=True,
        thinking=None,
        unsafe=False,
        verbose=False,
        extra_json=None,
        redact=False,
        dangerously_skip_permissions=False,
        auto_accept_edits=False,
        auto_approve=False,
        plan_mode="auto",
        max_repair_attempts=2,
        small_model=False,
        provider=provider,  # type: ignore[arg-type]
        api_key=command.config.api_key or os.environ.get("VILLANI_API_KEY"),
        villani_mode=command.mode == "villani",
        villani_objective=command.task if command.mode == "villani" else None,
    )
    runner.event_callback = event_callback
    runner.approval_callback = approval_callback
    # Villani mode normally auto-approves ASK decisions. The Pi bridge must preserve the
    # permission boundary and ask Pi instead of silently approving.
    runner.force_interactive_approvals = True
    return runner


def run_existing_runner(runner: Any, command: RunCommand) -> dict[str, Any]:
    budget = None
    if command.limits.max_turns is not None:
        budget = ExecutionBudget(
            max_turns=command.limits.max_turns,
            max_tool_calls=VILLANI_TASK_BUDGET.max_tool_calls,
            max_seconds=VILLANI_TASK_BUDGET.max_seconds,
            max_no_edit_turns=VILLANI_TASK_BUDGET.max_no_edit_turns,
            max_reconsecutive_recon_turns=VILLANI_TASK_BUDGET.max_reconsecutive_recon_turns,
        )
    if command.mode == "villani":
        result = runner.run_villani_mode()
    else:
        result = runner.run(command.task, execution_budget=budget) if budget is not None else runner.run(command.task)
    return result if isinstance(result, dict) else {"response": result}


def hash_files(repo: Path, files: list[str]) -> dict[str, str | None]:
    return {path: hash_file(repo / path) for path in files}


def hash_file(path: Path) -> str | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def attributed_changed_files(
    repo: Path,
    before_dirty: list[str],
    before_dirty_hashes: dict[str, str | None],
    touched_files: set[str],
) -> tuple[list[str], list[str]]:
    if not is_git_repo(repo):
        return sorted(path for path in touched_files if (repo / path).exists()), sorted(before_dirty)
    after_dirty = set(git_changed_files(repo))
    before_dirty_set = set(before_dirty)
    changed = set(after_dirty - before_dirty_set)
    for path in before_dirty_set & after_dirty:
        if hash_file(repo / path) != before_dirty_hashes.get(path):
            changed.add(path)
    for path in touched_files:
        normalized = path.replace("\\", "/").lstrip("./")
        if normalized in after_dirty:
            previous_hash = before_dirty_hashes.get(normalized)
            if normalized not in before_dirty_set or hash_file(repo / normalized) != previous_hash:
                changed.add(normalized)
    return sorted(changed), sorted(before_dirty_set)


def is_git_repo(repo: Path) -> bool:
    try:
        proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo, text=True, capture_output=True, timeout=10, check=False)
    except Exception:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def git_changed_files(repo: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.replace("\\", "/"))
    return sorted(set(paths))


def map_runner_event(run_id: str, event: dict[str, Any]) -> list[dict[str, Any]]:
    etype = str(event.get("type") or "")
    if etype in {"diagnosis_attempted", "diagnosis_generated", "planning_started", "model_request_started", "repair_attempt_started"}:
        return [{"type": "phase", "id": run_id, "phase": etype, "message": humanize_event_type(etype)}]
    if etype == "tool_started":
        tool = str(event.get("name") or "tool")
        tool_input = event.get("input") if isinstance(event.get("input"), dict) else {}
        return [{"type": "tool_started", "id": run_id, "tool": tool, "path": tool_input.get("path"), "command": tool_input.get("command")}]
    if etype == "tool_finished":
        tool = str(event.get("name") or "tool")
        ok = not bool(event.get("is_error"))
        events = [{"type": "tool_finished", "id": run_id, "tool": tool, "ok": ok, "summary": summarize_tool_event(event)}]
        if tool in {"Write", "Patch"}:
            tool_input = event.get("input") if isinstance(event.get("input"), dict) else {}
            path = tool_input.get("path") or tool_input.get("file_path")
            if path:
                events.append({"type": "workspace_changed", "id": run_id, "files": [str(path).replace("\\", "/")]})
        return events
    if etype == "validation_step_started":
        return [{"type": "verification_started", "id": run_id, "command": event.get("command") or event.get("name") or ""}]
    if etype == "validation_step_finished":
        passed = int(event.get("exit_code") or 0) == 0
        return [{"type": "verification_finished", "id": run_id, "command": event.get("command") or event.get("name") or "", "passed": passed, "summary": "passed" if passed else "failed"}]
    if etype == "validation_completed":
        passed = bool(event.get("passed"))
        return [{"type": "verification_finished", "id": run_id, "command": "validation", "passed": passed, "summary": "passed" if passed else "failed"}]
    if etype in {"command_wandering_detected", "progress_governor_redirected", "governor_redirect"}:
        return [{"type": "governor_redirect", "id": run_id, "message": str(event.get("message") or humanize_event_type(etype))}]
    return []


def humanize_event_type(etype: str) -> str:
    return etype.replace("_", " ").capitalize()


def summarize_tool_event(event: dict[str, Any]) -> str:
    tool = str(event.get("name") or "tool")
    tool_input = event.get("input") if isinstance(event.get("input"), dict) else {}
    if tool == "Bash" and tool_input.get("command"):
        return f"Ran {tool_input['command']}"
    path = tool_input.get("path") or tool_input.get("file_path")
    if path:
        return f"{tool} {path}"
    return f"{tool} finished"


def normalize_transcript_path(result: dict[str, Any]) -> str | None:
    path = result.get("transcript_path")
    return str(path) if path else None


def extract_summary(result: dict[str, Any]) -> str:
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    if execution.get("final_text"):
        return str(execution["final_text"])
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    content = response.get("content") if isinstance(response.get("content"), list) else []
    text = "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text").strip()
    return text[:2000]


def main_stdio() -> None:
    PiBridge().run_stdio()
