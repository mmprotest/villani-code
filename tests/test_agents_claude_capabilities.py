from __future__ import annotations

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
