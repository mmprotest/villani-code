from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any

from villani_code.integrations.pi_bridge import PiBridge, map_runner_event, summarize_approval_request
from villani_code.state import Runner
from villani_code import state_runtime


class DummyRunner:
    def __init__(self, events: list[dict[str, Any]] | None = None, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.event_callback = lambda _event: None
        self.events = events or []
        self.result = result or {
            "response": {"content": [{"type": "text", "text": "Fixed failing test."}]},
            "transcript_path": ".villani_code/runs/run/transcript.json",
            "execution": {"final_text": "Fixed failing test."},
        }
        self.error = error

    def run(self, instruction: str, **_kwargs: Any) -> dict[str, Any]:
        if self.error:
            raise self.error
        for event in self.events:
            self.event_callback(event)
        return self.result


def collect_json_lines(output: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]


def seed_planning_repo(repo: Path) -> None:
    (repo / "villani_code").mkdir(parents=True, exist_ok=True)
    (repo / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


class PlanningClient:
    def create_message(self, _payload: Any, stream: bool) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}


def test_bridge_planning_does_not_emit_execution_plan_approval(tmp_path: Path) -> None:
    seed_planning_repo(tmp_path)

    class PlanningRunner(Runner):
        def run(self, instruction: str, **_kwargs: Any) -> dict[str, Any]:
            state_runtime.ensure_project_memory_and_plan(self, instruction)
            return {
                "response": {"content": [{"type": "text", "text": "planned"}]},
                "execution": {"final_text": "planned"},
                "transcript_path": None,
            }

    def factory(_command: Any, event_callback: Any, approval_callback: Any) -> PlanningRunner:
        runner = PlanningRunner(client=PlanningClient(), repo=tmp_path, model="m", stream=False)
        runner.event_callback = event_callback
        runner.approval_callback = approval_callback
        return runner

    stdout = io.StringIO()
    bridge = PiBridge(stdout=stdout, runner_factory=factory)
    bridge._handle_command({"type": "run", "id": "run-plan", "task": "delete files and rewrite history", "repo": str(tmp_path)})
    deadline = time.time() + 2
    while bridge._active and time.time() < deadline:
        time.sleep(0.01)
    bridge._drain_events()

    events = collect_json_lines(stdout)
    assert any(event.get("type") == "phase" and event.get("phase") == "planning_started" for event in events)
    assert not any(event.get("type") == "approval_required" and event.get("tool") == "ExecutionPlan" for event in events)
    assert events[-1]["type"] == "run_completed"


def test_stdio_ping_emits_ready_and_pong() -> None:
    stdin = io.StringIO('{"type":"ping","id":"abc"}\n')
    stdout = io.StringIO()
    PiBridge(stdin=stdin, stdout=stdout).run_stdio()
    assert collect_json_lines(stdout) == [
        {"type": "ready", "protocol_version": 1},
        {"type": "pong", "id": "abc"},
    ]


def test_malformed_json_emits_error_and_processes_next_command() -> None:
    stdin = io.StringIO('{bad json}\n{"type":"ping","id":"still-alive"}\n')
    stdout = io.StringIO()
    PiBridge(stdin=stdin, stdout=stdout).run_stdio()
    events = collect_json_lines(stdout)
    assert events[0]["type"] == "ready"
    assert events[1]["type"] == "error"
    assert events[2] == {"type": "pong", "id": "still-alive"}


def test_unknown_command_emits_error() -> None:
    stdin = io.StringIO('{"type":"wat"}\n')
    stdout = io.StringIO()
    PiBridge(stdin=stdin, stdout=stdout).run_stdio()
    events = collect_json_lines(stdout)
    assert events[1]["type"] == "error"
    assert "Unknown bridge command type" in events[1]["error"]


def test_run_command_constructs_runner_and_completes(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def factory(command: Any, event_callback: Any) -> DummyRunner:
        seen["command"] = command
        runner = DummyRunner(
            events=[
                {"type": "diagnosis_attempted"},
                {"type": "tool_started", "name": "Read", "input": {"path": "src/foo.py"}},
                {"type": "tool_finished", "name": "Read", "input": {"path": "src/foo.py"}, "is_error": False},
                {"type": "validation_step_started", "name": "pytest", "command": "pytest tests/test_foo.py"},
                {"type": "validation_step_finished", "name": "pytest", "command": "pytest tests/test_foo.py", "exit_code": 0},
            ]
        )
        runner.event_callback = event_callback
        return runner

    stdout = io.StringIO()
    bridge = PiBridge(stdout=stdout, runner_factory=factory)
    bridge._handle_command(
        {
            "type": "run",
            "id": "run-123",
            "task": "Fix tests",
            "repo": str(tmp_path),
            "mode": "runner",
            "config": {"provider": "openai", "model": "m", "base_url": "http://localhost", "api_key": "dummy"},
            "limits": {"max_turns": 3},
        }
    )
    deadline = time.time() + 2
    while bridge._active and time.time() < deadline:
        time.sleep(0.01)
    bridge._drain_events()

    events = collect_json_lines(stdout)
    assert seen["command"].limits.max_turns == 3
    assert events[0]["type"] == "run_started"
    assert any(event["type"] == "phase" and event.get("phase") == "diagnosis_attempted" for event in events)
    assert any(event["type"] == "bridge_diagnostic" and "model configuration" in event.get("message", "") for event in events)
    assert any(event["type"] == "bridge_diagnostic" and "tool call requested" in event.get("message", "") for event in events)
    assert any(event["type"] == "verification_finished" and event["passed"] for event in events)
    assert events[-1]["type"] == "run_completed"
    assert events[-1]["transcript_path"] == ".villani_code/runs/run/transcript.json"


def test_failing_runner_emits_run_failed(tmp_path: Path) -> None:
    def factory(_command: Any, event_callback: Any) -> DummyRunner:
        runner = DummyRunner(error=RuntimeError("boom"))
        runner.event_callback = event_callback
        return runner

    stdout = io.StringIO()
    bridge = PiBridge(stdout=stdout, stderr=io.StringIO(), runner_factory=factory)
    bridge._handle_command({"type": "run", "id": "run-fail", "task": "Fix", "repo": str(tmp_path)})
    deadline = time.time() + 2
    while bridge._active and time.time() < deadline:
        time.sleep(0.01)
    bridge._drain_events()
    events = collect_json_lines(stdout)
    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_failed"
    assert events[-1]["error"] == "boom"


def test_abort_is_reported_after_cooperative_runner_stops(tmp_path: Path) -> None:
    started = threading.Event()

    class BlockingRunner(DummyRunner):
        def run(self, instruction: str, **_kwargs: Any) -> dict[str, Any]:
            started.set()
            while not bridge._active["run-abort"].abort_requested.is_set():
                time.sleep(0.01)
            return self.result

    def factory(_command: Any, event_callback: Any) -> BlockingRunner:
        runner = BlockingRunner()
        runner.event_callback = event_callback
        return runner

    stdout = io.StringIO()
    bridge = PiBridge(stdout=stdout, runner_factory=factory)
    bridge._handle_command({"type": "run", "id": "run-abort", "task": "Fix", "repo": str(tmp_path)})
    assert started.wait(timeout=2)
    bridge._handle_command({"type": "abort", "id": "run-abort"})
    active = next(iter(bridge._active.values()))
    assert active.thread is not None
    active.thread.join(timeout=2)
    bridge._drain_events()
    events = collect_json_lines(stdout)
    assert any(event["type"] == "abort_requested" for event in events)
    assert events[-1]["type"] == "run_aborted"


def test_runner_event_mapping_does_not_expose_prompts() -> None:
    mapped = map_runner_event("run-1", {"type": "stream_text", "text": "hidden prompt-ish text"})
    assert mapped == []

class EditingRunner(DummyRunner):
    def __init__(self, repo: Path, edits: dict[str, str], events: list[dict[str, Any]] | None = None) -> None:
        super().__init__(events=events)
        self.repo = repo
        self.edits = edits
        self.run_calls = 0
        self.villani_calls = 0

    def run(self, instruction: str, **_kwargs: Any) -> dict[str, Any]:
        self.run_calls += 1
        for rel, content in self.edits.items():
            target = self.repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self.event_callback({"type": "tool_finished", "name": "Write", "input": {"path": rel}, "is_error": False})
        return self.result

    def run_villani_mode(self) -> dict[str, Any]:
        self.villani_calls += 1
        return self.run("villani")


def init_git_repo(repo: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)


def commit_all(repo: Path) -> None:
    import subprocess

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def run_bridge_command(repo: Path, runner: DummyRunner, mode: str = "runner") -> dict[str, Any]:
    stdout = io.StringIO()

    def factory(_command: Any, event_callback: Any) -> DummyRunner:
        runner.event_callback = event_callback
        return runner

    bridge = PiBridge(stdout=stdout, runner_factory=factory)
    bridge._handle_command({"type": "run", "id": "run-change", "task": "Fix", "repo": str(repo), "mode": mode})
    deadline = time.time() + 2
    while bridge._active and time.time() < deadline:
        time.sleep(0.01)
    bridge._drain_events()
    return collect_json_lines(stdout)[-1]


def test_changed_files_clean_repo_new_change(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "src.py").write_text("old\n", encoding="utf-8")
    commit_all(tmp_path)

    event = run_bridge_command(tmp_path, EditingRunner(tmp_path, {"src.py": "new\n"}))

    assert event["type"] == "run_completed"
    assert event["changed_files"] == ["src.py"]
    assert event["preexisting_dirty_files"] == []


def test_changed_files_preexisting_dirty_unchanged_not_attributed(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("clean\n", encoding="utf-8")
    commit_all(tmp_path)
    (tmp_path / "notes.txt").write_text("dirty before\n", encoding="utf-8")

    event = run_bridge_command(tmp_path, EditingRunner(tmp_path, {}))

    assert event["changed_files"] == []
    assert event["preexisting_dirty_files"] == ["notes.txt"]


def test_changed_files_dirty_repo_new_clean_file_modified(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("clean\n", encoding="utf-8")
    (tmp_path / "src.py").write_text("old\n", encoding="utf-8")
    commit_all(tmp_path)
    (tmp_path / "notes.txt").write_text("dirty before\n", encoding="utf-8")

    event = run_bridge_command(tmp_path, EditingRunner(tmp_path, {"src.py": "new\n"}))

    assert event["changed_files"] == ["src.py"]
    assert event["preexisting_dirty_files"] == ["notes.txt"]


def test_changed_files_preexisting_dirty_modified_further(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("clean\n", encoding="utf-8")
    commit_all(tmp_path)
    (tmp_path / "notes.txt").write_text("dirty before\n", encoding="utf-8")

    event = run_bridge_command(tmp_path, EditingRunner(tmp_path, {"notes.txt": "dirty after villani\n"}))

    assert event["changed_files"] == ["notes.txt"]
    assert event["preexisting_dirty_files"] == ["notes.txt"]


def test_changed_files_change_then_revert_not_attributed(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "src.py").write_text("old\n", encoding="utf-8")
    commit_all(tmp_path)

    event = run_bridge_command(tmp_path, EditingRunner(tmp_path, {"src.py": "old\n"}))

    assert event["changed_files"] == []
    assert event["preexisting_dirty_files"] == []


def test_mode_runner_calls_run_and_villani_calls_run_villani(tmp_path: Path) -> None:
    runner = EditingRunner(tmp_path, {})
    run_bridge_command(tmp_path, runner, mode="runner")
    assert runner.run_calls == 1
    assert runner.villani_calls == 0

    villani_runner = EditingRunner(tmp_path, {})
    run_bridge_command(tmp_path, villani_runner, mode="villani")
    assert villani_runner.villani_calls == 1


class ApprovalRunner(DummyRunner):
    def __init__(self, repo: Path, tool: str, payload: dict[str, Any]) -> None:
        super().__init__()
        self.repo = repo
        self.tool = tool
        self.payload = payload
        self.approval_callback = lambda _name, _input: False
        self.executed = threading.Event()
        self.denied = threading.Event()

    def run(self, instruction: str, **_kwargs: Any) -> dict[str, Any]:
        approved = self.approval_callback(self.tool, self.payload)
        self.event_callback({"type": "approval_resolved", "name": self.tool, "input": self.payload, "approved": approved})
        if not approved:
            self.denied.set()
            return {"response": {"content": [{"type": "text", "text": "Denied."}]}, "execution": {"final_text": "Denied."}}
        if self.tool == "Bash":
            marker = self.repo / "bash-ran.txt"
            marker.write_text(str(self.payload.get("command", "")), encoding="utf-8")
        else:
            path = self.payload.get("path") or self.payload.get("file_path")
            if path:
                target = self.repo / str(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(self.payload.get("content", "approved")), encoding="utf-8")
                self.event_callback({"type": "tool_finished", "name": self.tool, "input": self.payload, "is_error": False})
        self.executed.set()
        return self.result


def start_approval_bridge(repo: Path, runner: ApprovalRunner) -> tuple[PiBridge, io.StringIO]:
    stdout = io.StringIO()

    def factory(_command: Any, event_callback: Any) -> ApprovalRunner:
        runner.event_callback = event_callback
        return runner

    bridge = PiBridge(stdout=stdout, runner_factory=factory)
    bridge._handle_command({"type": "run", "id": "run-approval", "task": "Fix", "repo": str(repo), "mode": "runner"})
    return bridge, stdout


def wait_for_event(bridge: PiBridge, output: io.StringIO, event_type: str, timeout: float = 2) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        bridge._drain_events()
        events = collect_json_lines(output)
        for event in events:
            if event.get("type") == event_type:
                return event
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {event_type}; saw {collect_json_lines(output)}")


def finish_bridge(bridge: PiBridge, timeout: float = 2) -> None:
    deadline = time.time() + timeout
    while bridge._active and time.time() < deadline:
        time.sleep(0.01)
    bridge._drain_events()


def test_approval_required_write_blocks_until_approved(tmp_path: Path) -> None:
    runner = ApprovalRunner(tmp_path, "Write", {"path": "src/foo.py", "content": "approved"})
    bridge, stdout = start_approval_bridge(tmp_path, runner)

    approval = wait_for_event(bridge, stdout, "approval_required")
    assert approval["id"] == "run-approval"
    assert approval["tool"] == "Write"
    assert approval["input"] == {"path": "src/foo.py", "content_chars": 8}
    assert "src/foo.py" in approval["summary"]
    assert not (tmp_path / "src/foo.py").exists()

    bridge._handle_command({"type": "approval_response", "id": "run-approval", "request_id": approval["request_id"], "approved": True})
    finish_bridge(bridge)

    assert runner.executed.is_set()
    assert (tmp_path / "src/foo.py").read_text(encoding="utf-8") == "approved"
    events = collect_json_lines(stdout)
    assert any(event["type"] == "approval_resolved" and event["approved"] is True for event in events)
    assert events[-1]["type"] == "run_completed"
    assert events[-1]["changed_files"] == ["src/foo.py"]


def test_rejected_write_is_blocked(tmp_path: Path) -> None:
    runner = ApprovalRunner(tmp_path, "Write", {"path": "blocked.txt", "content": "nope"})
    bridge, stdout = start_approval_bridge(tmp_path, runner)

    approval = wait_for_event(bridge, stdout, "approval_required")
    bridge._handle_command({"type": "approval_response", "id": "run-approval", "request_id": approval["request_id"], "approved": False})
    finish_bridge(bridge)

    assert runner.denied.is_set()
    assert not runner.executed.is_set()
    assert not (tmp_path / "blocked.txt").exists()
    events = collect_json_lines(stdout)
    assert any(event["type"] == "approval_resolved" and event["approved"] is False for event in events)
    assert events[-1]["changed_files"] == []


def test_bash_approval_shows_command_and_blocks_execution(tmp_path: Path) -> None:
    command = "pip install package-name"
    runner = ApprovalRunner(tmp_path, "Bash", {"command": command})
    bridge, stdout = start_approval_bridge(tmp_path, runner)

    approval = wait_for_event(bridge, stdout, "approval_required")
    assert approval["tool"] == "Bash"
    assert approval["input"] == {"command": command}
    assert command in approval["summary"]
    assert not (tmp_path / "bash-ran.txt").exists()

    bridge._handle_command({"type": "approval_response", "id": "run-approval", "request_id": approval["request_id"], "approved": True})
    finish_bridge(bridge)

    assert runner.executed.is_set()
    assert (tmp_path / "bash-ran.txt").read_text(encoding="utf-8") == command


def test_abort_while_approval_pending_denies_and_aborts(tmp_path: Path) -> None:
    runner = ApprovalRunner(tmp_path, "Write", {"path": "abort.txt", "content": "no"})
    bridge, stdout = start_approval_bridge(tmp_path, runner)

    wait_for_event(bridge, stdout, "approval_required")
    bridge._handle_command({"type": "abort", "id": "run-approval"})
    finish_bridge(bridge)

    assert runner.denied.is_set()
    assert not runner.executed.is_set()
    assert not (tmp_path / "abort.txt").exists()
    events = collect_json_lines(stdout)
    assert any(event["type"] == "abort_requested" for event in events)
    assert events[-1]["type"] == "run_aborted"


def test_unknown_duplicate_and_malformed_approval_responses_are_safe(tmp_path: Path) -> None:
    runner = ApprovalRunner(tmp_path, "Write", {"path": "once.txt", "content": "once"})
    bridge, stdout = start_approval_bridge(tmp_path, runner)
    approval = wait_for_event(bridge, stdout, "approval_required")

    bridge._handle_command({"type": "approval_response", "id": "run-approval", "request_id": "missing", "approved": True})
    assert collect_json_lines(stdout)[-1]["type"] == "error"

    bridge._handle_command({"type": "approval_response", "id": "run-approval", "request_id": approval["request_id"], "approved": True})
    bridge._handle_command({"type": "approval_response", "id": "run-approval", "request_id": approval["request_id"], "approved": False})
    finish_bridge(bridge)

    assert (tmp_path / "once.txt").read_text(encoding="utf-8") == "once"
    events = collect_json_lines(stdout)
    assert sum(1 for event in events if event.get("type") == "approval_resolved" and event.get("request_id") == approval["request_id"]) == 1
    assert any(event["type"] == "error" and "Unknown approval request" in event["error"] for event in events)

    try:
        bridge._handle_command({"type": "approval_response", "id": "run-approval", "request_id": "x", "approved": "yes"})
    except ValueError as exc:
        bridge.emit({"type": "error", "error": str(exc)})
    assert "requires boolean approved" in collect_json_lines(stdout)[-1]["error"]


def test_approval_request_summary_is_bounded() -> None:
    summary, safe_input = summarize_approval_request("Write", {"path": "a.py", "content": "x" * 5000})
    assert summary == "Write file: a.py"
    assert safe_input == {"path": "a.py", "content_chars": 5000}

    bash_summary, bash_input = summarize_approval_request("Bash", {"command": "x" * 3000})
    assert len(bash_input["command"]) == 2000
    assert bash_input["command"].endswith("…")
    assert bash_summary.startswith("Run command: ")
