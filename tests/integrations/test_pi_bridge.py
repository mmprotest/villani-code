from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any

from villani_code.integrations.pi_bridge import PiBridge, map_runner_event


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
    assert [event["type"] for event in events][:2] == ["run_started", "phase"]
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
