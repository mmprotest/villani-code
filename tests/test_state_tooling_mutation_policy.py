from __future__ import annotations

import json
from pathlib import Path

from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy


class _Client:
    def create_message(self, _payload, stream):
        assert stream is False
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


class _Hooks:
    def run_event(self, *_args, **_kwargs):
        return type("Hook", (), {"allow": True, "reason": ""})()


class _PermissivePermissions:
    def evaluate_with_reason(self, *_args, **_kwargs):
        from villani_code.permissions import Decision

        return type("P", (), {"decision": Decision.ALLOW, "reason": ""})()


def _runner(tmp_path: Path) -> Runner:
    runner = Runner(client=_Client(), repo=tmp_path, model="m", stream=False, plan_mode="off")
    runner.hooks = _Hooks()
    runner.permissions = _PermissivePermissions()
    return runner


def test_write_new_file_still_allowed(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = execute_tool_with_policy(runner, "Write", {"file_path": "new.txt", "content": "x\n"}, "1", 0)
    assert result["is_error"] is False
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "x\n"


def test_write_existing_file_small_change_transforms_to_patch(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")
    result = execute_tool_with_policy(runner, "Write", {"file_path": "a.txt", "content": "one\nthree\n"}, "1", 0)
    assert result["is_error"] is False
    assert "Patch applied" in str(result["content"])
    assert target.read_text(encoding="utf-8") == "one\nthree\n"


def test_write_existing_file_large_rewrite_rejected_with_clear_message(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("\n".join(f"line {i}" for i in range(1, 260)) + "\n", encoding="utf-8")
    replacement = "\n".join(f"new {i}" for i in range(1, 260)) + "\n"
    result = execute_tool_with_policy(runner, "Write", {"file_path": "a.txt", "content": replacement}, "1", 0)
    assert result["is_error"] is True
    assert "Rewrite-heavy mutation rejected" in str(result["content"])
    assert "Emit a narrow Patch" in str(result["content"])


def test_patch_payload_with_git_header_and_prose_is_normalized(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("hello\n", encoding="utf-8")
    diff = (
        "Please apply this:\n"
        "```diff\n"
        "diff --git a/a.txt b/a.txt\n"
        "index 111..222 100644\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1 +1 @@\n"
        "-hello\n"
        "+world\n"
        "```\n"
        "Thanks.\n"
    )
    result = execute_tool_with_policy(runner, "Patch", {"unified_diff": diff}, "1", 0)
    assert result["is_error"] is False
    assert target.read_text(encoding="utf-8") == "world\n"


def test_write_fenced_block_extraction_for_non_python_file(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = execute_tool_with_policy(
        runner,
        "Write",
        {
            "file_path": "README.md",
            "content": "Use this content:\n```markdown\n# Title\n\nhello\n```\n",
        },
        "1",
        0,
    )
    assert result["is_error"] is False
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "# Title\n\nhello\n"


def test_patch_without_file_path_tracks_targets_and_before_contents(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("x=0\n", encoding="utf-8")
    diff = "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x=0\n+x=1\n"
    result = execute_tool_with_policy(runner, "Patch", {"unified_diff": diff}, "1", 2)
    assert result["is_error"] is False
    assert "src/a.py" in runner._intended_targets
    assert runner._current_verification_targets == {"src/a.py"}
    assert runner._current_verification_before_contents.get("src/a.py") == "x=0\n"


def test_patch_existing_file_rewrite_heavy_is_rejected(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("\n".join(f"line {i}" for i in range(1, 200)) + "\n", encoding="utf-8")
    replacement = "\n".join(f"new {i}" for i in range(1, 200)) + "\n"
    diff_lines = [
        "--- a/a.txt",
        "+++ b/a.txt",
        "@@ -1,199 +1,199 @@",
        *[f"-line {i}" for i in range(1, 200)],
        *[f"+new {i}" for i in range(1, 200)],
        "",
    ]
    result = execute_tool_with_policy(runner, "Patch", {"unified_diff": "\n".join(diff_lines)}, "1", 0)
    assert result["is_error"] is True
    assert "Rewrite-heavy mutation rejected" in str(result["content"])


def test_failed_bash_command_is_blocked_on_immediate_retry(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    command = "python -c \"import sys; sys.exit(1)\""
    first = execute_tool_with_policy(runner, "Bash", {"command": command}, "1", 0)
    assert first["is_error"] is False
    assert json.loads(str(first["content"]))["exit_code"] != 0
    second = execute_tool_with_policy(runner, "Bash", {"command": command}, "2", 0)
    assert second["is_error"] is True
    assert "already failed" in str(second["content"])


def test_failed_bash_retry_block_normalizes_whitespace(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    first = execute_tool_with_policy(runner, "Bash", {"command": "  python  -c \"import sys; sys.exit(1)\"  "}, "1", 0)
    assert json.loads(str(first["content"]))["exit_code"] != 0
    second = execute_tool_with_policy(runner, "Bash", {"command": "python -c   \"import sys; sys.exit(1)\""}, "2", 0)
    assert second["is_error"] is True
    assert "already failed" in str(second["content"])


def test_failed_bash_retry_block_is_case_insensitive_on_windows(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._shell_retry_case_insensitive = True
    first = execute_tool_with_policy(runner, "Bash", {"command": "PyThOn -c \"import sys; sys.exit(1)\""}, "1", 0)
    assert json.loads(str(first["content"]))["exit_code"] != 0
    second = execute_tool_with_policy(runner, "Bash", {"command": "python -C \"import sys; sys.exit(1)\""}, "2", 0)
    assert second["is_error"] is True


def test_materially_different_bash_command_is_allowed_after_failure(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    failed = execute_tool_with_policy(runner, "Bash", {"command": "python -c \"import sys; sys.exit(1)\""}, "1", 0)
    assert json.loads(str(failed["content"]))["exit_code"] != 0
    allowed = execute_tool_with_policy(runner, "Bash", {"command": "python -c \"print('ok')\""}, "2", 0)
    assert allowed["is_error"] is False
    assert json.loads(str(allowed["content"]))["exit_code"] == 0


def test_successful_bash_command_clears_retry_block(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    command = "python -c \"import sys; sys.exit(1)\""
    failed = execute_tool_with_policy(runner, "Bash", {"command": command}, "1", 0)
    assert json.loads(str(failed["content"]))["exit_code"] != 0
    success = execute_tool_with_policy(runner, "Bash", {"command": "python -c \"print('reset')\""}, "2", 0)
    assert json.loads(str(success["content"]))["exit_code"] == 0
    retry = execute_tool_with_policy(runner, "Bash", {"command": command}, "3", 0)
    assert retry["is_error"] is False
    assert json.loads(str(retry["content"]))["exit_code"] != 0


def test_non_shell_tools_are_unaffected_by_failed_shell_retry_memory(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    _ = execute_tool_with_policy(runner, "Bash", {"command": "python -c \"import sys; sys.exit(1)\""}, "1", 0)
    read = execute_tool_with_policy(runner, "Read", {"file_path": "missing.txt"}, "2", 0)
    assert read["is_error"] is True
    assert "No such file" in str(read["content"])
