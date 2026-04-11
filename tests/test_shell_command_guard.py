from __future__ import annotations

import json
from pathlib import Path

from villani_code.shells import classify_and_rewrite_command, detect_shell_environment
from villani_code.tools import execute_tool


def test_env_detection_returns_shell_family(tmp_path: Path) -> None:
    env = detect_shell_environment(str(tmp_path))
    assert env.shell_family in {"cmd", "powershell", "bash", "zsh", "unknown"}


def test_cmd_rm_rewrites_to_del(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("rm foo.txt", "cmd")
    assert decision.classification == "needs_rewrite"
    assert decision.command == "del /q foo.txt"


def test_cmd_grep_rewrites_to_findstr(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("grep hello notes.txt", "cmd")
    assert decision.classification == "needs_rewrite"
    assert decision.command == 'findstr /n /c:"hello" notes.txt'


def test_cmd_tail_rewrites_to_powershell_get_content(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("tail -n 20 app.log", "cmd")
    assert decision.classification == "needs_rewrite"
    assert decision.command == 'powershell -NoProfile -Command "Get-Content \'app.log\' -Tail 20"'


def test_cmd_heredoc_blocked(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("cat <<EOF\nhello\nEOF", "cmd")
    assert decision.classification == "blocked"
    assert decision.offending_pattern == "<<EOF"


def test_cmd_embedded_head_pipeline_is_blocked(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("echo ok && type app.log | head -50", "cmd")
    assert decision.classification == "blocked"
    assert decision.offending_token == "head"


def test_cmd_head_n_rewrites_to_powershell_totalcount(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("head -n 20 app.log", "cmd")
    assert decision.classification == "needs_rewrite"
    assert decision.command == 'powershell -NoProfile -Command "Get-Content \'app.log\' -TotalCount 20"'


def test_cmd_head_default_rewrites_to_powershell_totalcount_10(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("head app.log", "cmd")
    assert decision.classification == "needs_rewrite"
    assert decision.command == 'powershell -NoProfile -Command "Get-Content \'app.log\' -TotalCount 10"'


def test_cmd_heredoc_anywhere_is_blocked(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("echo start && python - <<'EOF'\nprint(1)\nEOF", "cmd")
    assert decision.classification == "blocked"
    assert decision.offending_token == "<<"


def test_cmd_classifier_scans_full_command_string(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("echo ok && dir | head -30", "cmd")
    assert decision.classification == "blocked"


def test_powershell_grep_rewrites_to_select_string(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("grep hello notes.txt", "powershell")
    assert decision.classification == "needs_rewrite"
    assert decision.command == 'Select-String -Pattern "hello" notes.txt'


def test_bash_native_commands_are_unchanged(tmp_path: Path) -> None:
    decision = classify_and_rewrite_command("tail -n 2 app.log", "bash")
    assert decision.classification == "allowed"
    assert decision.command == "tail -n 2 app.log"


def test_blocked_commands_produce_compact_structured_feedback(tmp_path: Path) -> None:
    result = execute_tool(
        "Bash",
        {"command": "cat <<EOF\nhello\nEOF", "cwd": ".", "timeout_sec": 5},
        tmp_path,
        runtime_state={"shell_environment": {"shell_family": "cmd"}},
    )
    assert result["is_error"] is True
    payload = json.loads(result["content"])
    assert payload["shell_family"] == "cmd"
    assert payload["classification"] == "blocked"
    assert payload["offending_token"] == "<<"
    assert payload["offending_pattern"] == "<<EOF"
    assert "short_reason" in payload
