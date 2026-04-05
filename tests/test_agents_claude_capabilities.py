from __future__ import annotations

import shlex
import subprocess

from villani_code.benchmark.agents.claude_code import ClaudeCodeAgentRunner


def test_detect_cli_capabilities_from_help_text(monkeypatch) -> None:
    ClaudeCodeAgentRunner._capability_cache = None

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["claude", "--help"],
            returncode=0,
            stdout="--bare --settings --allowedTools --output-format",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    caps = ClaudeCodeAgentRunner.detect_cli_capabilities()
    assert caps["bare"] is True
    assert caps["settings"] is True
    assert caps["allowed_tools"] is True
    assert caps["output_format"] is True
    assert caps["include_hook_events"] is False


def test_apply_capabilities_keeps_permission_mode_and_adds_allowed_tools() -> None:
    runner = ClaudeCodeAgentRunner()
    command = [
        "claude",
        "--model",
        "claude-3-7-sonnet",
        "--bare",
        "--print",
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "fix bug",
    ]
    updated = runner._apply_capabilities_to_command(
        command,
        {
            "bare": True,
            "settings": True,
            "include_hook_events": False,
            "allowed_tools": True,
            "output_format": True,
        },
        deep_debug=False,
    )
    assert updated[-1] == "fix bug"
    permission_idx = updated.index("--permission-mode")
    assert updated[permission_idx + 1] == "bypassPermissions"
    allowed_idx = updated.index("--allowedTools")
    assert updated[allowed_idx + 1 : allowed_idx + 5] == ["Read", "Edit", "Write", "Bash"]


def test_apply_capabilities_replaces_existing_allowed_tools_block_without_csv() -> None:
    runner = ClaudeCodeAgentRunner()
    command = [
        "claude",
        "--model",
        "claude-3-7-sonnet",
        "--print",
        "--permission-mode",
        "bypassPermissions",
        "--allowedTools",
        "Read,Edit,Write,Bash",
        "--settings",
        "/tmp/settings.json",
        "edit file",
    ]
    updated = runner._apply_capabilities_to_command(
        command,
        {
            "bare": False,
            "settings": True,
            "include_hook_events": False,
            "allowed_tools": True,
            "output_format": False,
        },
        deep_debug=False,
    )
    joined = shlex.join(updated)
    assert "Read,Edit,Write,Bash" not in updated
    assert "--allowedTools Read Edit Write Bash" in joined
    assert updated.count("--allowedTools") == 1
    assert updated[-1] == "edit file"
