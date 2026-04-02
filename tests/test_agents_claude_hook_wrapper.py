from __future__ import annotations

import json
import sys
from pathlib import Path

from villani_code.benchmark.agents import claude_hook_wrapper


def test_wrapper_writes_start_and_success_breadcrumbs(tmp_path: Path, monkeypatch) -> None:
    events = tmp_path / "claude_hook_events.jsonl"
    errors = tmp_path / "claude_hook_events.err"
    breadcrumbs = tmp_path / "claude_hook_events.breadcrumbs.log"
    monkeypatch.setattr(sys, "stdin", type("FakeStdin", (), {"read": lambda self: '{"hook_event_name":"PostToolUse"}'})())
    code = claude_hook_wrapper.main(["wrapper", str(events), str(errors), str(breadcrumbs)])
    assert code == 0
    lines = breadcrumbs.read_text(encoding="utf-8").splitlines()
    assert any("hook_wrapper_start" in line for line in lines)
    assert any("hook_wrapper_success" in line for line in lines)
    assert events.exists()


def test_wrapper_writes_event_jsonl_on_success(tmp_path: Path, monkeypatch) -> None:
    events = tmp_path / "claude_hook_events.jsonl"
    errors = tmp_path / "claude_hook_events.err"
    breadcrumbs = tmp_path / "claude_hook_events.breadcrumbs.log"
    monkeypatch.setattr(sys, "stdin", type("FakeStdin", (), {"read": lambda self: '{"tool_name":"Write","tool_input":{"file_path":"a.txt"}}'})())
    code = claude_hook_wrapper.main(["wrapper", str(events), str(errors), str(breadcrumbs)])
    assert code == 0
    record = json.loads(events.read_text(encoding="utf-8").splitlines()[0])
    assert record["tool_name"] == "Write"


def test_wrapper_writes_error_log_on_exception(tmp_path: Path, monkeypatch) -> None:
    events = tmp_path / "claude_hook_events.jsonl"
    errors = tmp_path / "claude_hook_events.err"
    breadcrumbs = tmp_path / "claude_hook_events.breadcrumbs.log"

    class BrokenStdin:
        def read(self) -> str:
            raise RuntimeError("forced boom")

    monkeypatch.setattr(sys, "stdin", BrokenStdin())
    code = claude_hook_wrapper.main(["wrapper", str(events), str(errors), str(breadcrumbs)])
    assert code == 1
    assert "forced boom" in errors.read_text(encoding="utf-8")
