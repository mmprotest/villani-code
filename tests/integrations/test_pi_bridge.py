from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from villani_code.integrations.pi_bridge import PiBridge


class DummyRunner:
    print_stream = False

    def __init__(self, approval_callback=None, event_callback=None):
        self.approval_callback = approval_callback
        self.event_callback = event_callback

    def run(self, task, execution_budget=None):
        if task == "approve-twice":
            assert self.approval_callback("Bash", {"command": "one"}) is True
            assert self.approval_callback("Bash", {"command": "two"}) is True
        return {"response": "ok", "execution": {"completed": True}}


def _bridge():
    out = io.StringIO()

    def factory(command, event_callback, approval_callback):
        return DummyRunner(approval_callback=approval_callback, event_callback=event_callback)

    return PiBridge(stdin=io.StringIO(), stdout=out, runner_factory=factory), out


def _events(out: io.StringIO):
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_ping_and_ready_event_shape():
    bridge, out = _bridge()
    bridge.emit({"type": "ready", "protocol_version": 1})
    bridge.handle({"type": "ping", "id": "p1"})
    assert _events(out) == [{"type": "ready", "protocol_version": 1}, {"type": "pong", "id": "p1"}]


def test_approval_request_ids_are_monotonic_even_after_resolution():
    bridge, out = _bridge()
    repo = tempfile.TemporaryDirectory()
    bridge.handle({"type": "run", "id": "r1", "task": "approve-twice", "repo": repo.name, "config": {"provider": "openai", "model": "m", "base_url": "u"}})  # type: ignore[arg-type]
    while True:
        approvals = [e for e in _events(out) if e.get("type") == "approval_required"]
        if approvals:
            break
    bridge.approval(type("Cmd", (), {"id": "r1", "request_id": "r1:1", "approved": True})())
    while True:
        approvals = [e for e in _events(out) if e.get("type") == "approval_required"]
        if len(approvals) == 2:
            break
    bridge.approval(type("Cmd", (), {"id": "r1", "request_id": "r1:2", "approved": True})())
    bridge.runs["r1"].thread.join(2)
    approvals = [e["request_id"] for e in _events(out) if e.get("type") == "approval_required"]
    assert approvals == ["r1:1", "r1:2"]
    repo.cleanup()


def test_abort_resolves_pending_approval_and_emits_run_aborted():
    bridge, out = _bridge()
    repo = tempfile.TemporaryDirectory()
    bridge.handle({"type": "run", "id": "r2", "task": "approve-twice", "repo": repo.name, "config": {"provider": "openai", "model": "m", "base_url": "u"}})  # type: ignore[arg-type]
    while not [e for e in _events(out) if e.get("type") == "approval_required"]:
        pass
    bridge.abort("r2")
    bridge.runs["r2"].thread.join(2)
    types = [e["type"] for e in _events(out)]
    assert "abort_requested" in types
    assert "run_aborted" in types
    repo.cleanup()
