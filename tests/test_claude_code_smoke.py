from __future__ import annotations

import os

import pytest

from villani_code.benchmark.agents.claude_code_smoke import run_claude_code_smoke_task


@pytest.mark.skipif(os.environ.get("RUN_CLAUDE_CODE_SMOKE") != "1", reason="opt-in smoke test")
def test_claude_code_smoke_task_runs_when_opted_in() -> None:
    model = os.environ.get("CLAUDE_CODE_SMOKE_MODEL", "claude-3-7-sonnet")
    sentinel = run_claude_code_smoke_task(model=model)
    assert sentinel.read_text(encoding="utf-8").strip() == "smoke-ok"
