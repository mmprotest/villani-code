from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.agents.claude_code import ClaudeCodeAgentRunner


def test_hook_settings_command_is_shell_string(tmp_path: Path) -> None:
    settings = ClaudeCodeAgentRunner._hook_settings(
        tmp_path / "events.jsonl",
        tmp_path / "events.err",
        tmp_path / "events.breadcrumbs.log",
    )
    command = settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert isinstance(command, str)
    assert "claude_hook_wrapper.py" in command


def test_format_command_windows_uses_list2cmdline() -> None:
    command = ClaudeCodeAgentRunner._format_command(
        ["python.exe", "C:\\Program Files\\hook.py", "C:\\tmp\\events.jsonl"],
        is_windows=True,
    )
    assert '"' in command
    assert "Program Files" in command


def test_format_command_unix_uses_shell_join() -> None:
    command = ClaudeCodeAgentRunner._format_command(
        ["/usr/bin/python3", "/tmp/hook logger.py", "/tmp/events.jsonl"],
        is_windows=False,
    )
    assert "'/tmp/hook logger.py'" in command
