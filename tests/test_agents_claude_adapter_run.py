from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from villani_code.benchmark.agents.claude_code import ClaudeCodeAgentRunner


def _make_fake_claude(tmp_path: Path) -> Path:
    script = tmp_path / "claude"
    script.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
prompt = args[-1]
settings_path = Path(args[args.index('--settings') + 1])
settings = json.loads(settings_path.read_text(encoding='utf-8'))

hook_target = None
for group in settings.get('hooks', {}).values():
    for entry in group:
        command = entry.get('hooks', [{}])[0].get('command')
        if isinstance(command, list) and command:
            hook_target = command[-1]

if prompt == 'edit-file':
    Path('edited.txt').write_text('edited\\n', encoding='utf-8')
    print(json.dumps({'result': ''}))
elif prompt == 'emit-hooks':
    if hook_target:
        with Path(hook_target).open('a', encoding='utf-8') as f:
            f.write(json.dumps({'timestamp': 1.0, 'hook_event_name': 'PostToolUse', 'tool_name': 'Write', 'tool_input': {'file_path': 'hook.txt'}}) + '\\n')
            f.write(json.dumps({'timestamp': 2.0, 'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'pytest -q'}}) + '\\n')
            f.write(json.dumps({'timestamp': 3.0, 'hook_event_name': 'PostToolUseFailure', 'tool_name': 'Bash', 'error': 'boom'}) + '\\n')
    print(json.dumps({'result': ''}))
elif prompt == 'stdout-diff':
    diff = '--- a/patch.txt\\n+++ b/patch.txt\\n@@ -0,0 +1 @@\\n+patched\\n'
    print(json.dumps({'result': diff}))
elif prompt == 'no-op':
    print(json.dumps({'result': ''}))
else:
    print(json.dumps({'result': ''}))
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _run(tmp_path: Path, prompt: str) -> tuple[Path, object]:
    repo = tmp_path / f"repo-{prompt}"
    repo.mkdir()
    (repo / "patch.txt").write_text("", encoding="utf-8")
    debug = tmp_path / f"debug-{prompt}"
    debug.mkdir()
    runner = ClaudeCodeAgentRunner()
    result = runner.run_agent(
        repo_path=repo,
        prompt=prompt,
        model="claude-3-7-sonnet",
        base_url=None,
        api_key=None,
        provider="anthropic",
        timeout=15,
        debug_dir=debug,
    )
    return repo, result


def test_claude_adapter_detects_workspace_edit_without_stdout_diff(tmp_path: Path, monkeypatch) -> None:
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _make_fake_claude(fake_dir)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    repo, result = _run(tmp_path, "edit-file")
    assert (repo / "edited.txt").exists()
    assert any(event.type == "apply_patch" for event in result.events)
    assert any(event.type == "write_file" for event in result.events)


def test_claude_adapter_uses_hook_events_for_telemetry(tmp_path: Path, monkeypatch) -> None:
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _make_fake_claude(fake_dir)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    _repo, result = _run(tmp_path, "emit-hooks")
    types = [event.type for event in result.events]
    assert "shell_command" in types
    assert "command_failed" in types
    assert result.telemetry_field_quality_map["tool_calls_total"].value in {"exact", "inferred"}


def test_claude_adapter_stdout_diff_fallback_applies_patch(tmp_path: Path, monkeypatch) -> None:
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _make_fake_claude(fake_dir)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    repo, result = _run(tmp_path, "stdout-diff")
    assert (repo / "patch.txt").read_text(encoding="utf-8") == "patched\n"
    assert any(event.payload.get("source") == "stdout_diff" for event in result.events)


def test_claude_adapter_noop_keeps_no_patch_signals(tmp_path: Path, monkeypatch) -> None:
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _make_fake_claude(fake_dir)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    _repo, result = _run(tmp_path, "no-op")
    assert not any(event.type in {"apply_patch", "write_file", "file_edit"} for event in result.events)


def test_claude_adapter_captures_failed_bash_hook(tmp_path: Path, monkeypatch) -> None:
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _make_fake_claude(fake_dir)
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    _repo, result = _run(tmp_path, "emit-hooks")
    failures = [event for event in result.events if event.type == "command_failed"]
    assert failures
    assert "boom" in str(failures[0].payload.get("error"))
