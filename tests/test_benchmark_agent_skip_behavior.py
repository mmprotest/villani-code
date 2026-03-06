from pathlib import Path

from villani_code.benchmark.adapters.base import AgentAdapterConfig
from villani_code.benchmark.adapters.claude_code import ClaudeCodeAdapter
from villani_code.benchmark.models import BenchmarkTask


def test_claude_adapter_skips_when_binary_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("villani_code.benchmark.adapters.claude_code.command_exists", lambda name: False)
    adapter = ClaudeCodeAdapter(AgentAdapterConfig(agent_name="claude-code"))
    task = BenchmarkTask(
        id="t1",
        name="task",
        instruction="do thing",
        category="test",
    )
    result = adapter.run_task(task, tmp_path, tmp_path)
    assert result.skipped is True
    assert "executable not installed" in result.exit_reason
