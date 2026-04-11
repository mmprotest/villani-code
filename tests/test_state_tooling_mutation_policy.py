from __future__ import annotations

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


def test_recovery_mode_blocks_delete_of_active_failing_file(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "legal_review_app.py"
    target.write_text("print('x')\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._failing_file = "legal_review_app.py"
    runner._failing_error_summary = "SyntaxError"
    runner._file_was_read_since_failure = True

    result = execute_tool_with_policy(
        runner,
        "Patch",
        {"unified_diff": "--- a/legal_review_app.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-print('x')\n"},
        "1",
        0,
    )
    assert result["is_error"] is True
    assert '"recovery_blocked": true' in str(result["content"])
    assert "delete_blocked_for_failing_file" in str(result["content"])


def test_recovery_mode_blocks_delete_of_active_solution_file(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "web_app.py"
    target.write_text("print('x')\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._active_solution_file = "web_app.py"
    runner._failing_file = "helper.py"
    runner._file_was_read_since_failure = True

    result = execute_tool_with_policy(
        runner,
        "Patch",
        {"unified_diff": "--- a/web_app.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-print('x')\n"},
        "1",
        0,
    )
    assert result["is_error"] is True
    assert "delete_blocked_for_failing_file" in str(result["content"])
    assert '"recovery_blocked": true' in str(result["content"])


def test_recovery_mode_blocks_edit_without_read_first(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "legal_review_app.py"
    target.write_text("print('x')\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._failing_file = "legal_review_app.py"
    runner._failing_error_summary = "NameError"
    runner._file_was_read_since_failure = False

    result = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "legal_review_app.py", "content": "print('fixed')\n"},
        "1",
        0,
    )
    assert result["is_error"] is True
    assert "read_required_before_edit" in str(result["content"])


def test_recovery_mode_blocks_new_validation_artifact_entrypoint_launch(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "web_app.py").write_text("print('x')\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._active_solution_file = "web_app.py"
    runner._primary_execution_target = "web_app.py"
    runner._recovery_files_at_failure = {"web_app.py"}

    create = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "web_server.py", "content": "print('x')\n"},
        "1a",
        0,
    )
    assert create["is_error"] is False

    result = execute_tool_with_policy(
        runner,
        "Bash",
        {"command": "python web_server.py", "timeout_sec": 30},
        "1",
        0,
    )
    assert result["is_error"] is True
    assert "recovery_new_validation_artifact_blocked" in str(result["content"])
    assert "primary_execution_target=web_app.py" in str(result["content"])


def test_recovery_mode_allows_rerun_of_same_primary_target(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "web_app.py").write_text("print('x')\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._primary_execution_target = "web_app.py"
    runner._active_solution_file = "web_app.py"

    result = execute_tool_with_policy(
        runner,
        "Bash",
        {"command": "python web_app.py", "timeout_sec": 30},
        "1",
        0,
    )
    assert result["is_error"] is False


def test_recovery_mode_allows_helper_file_edit_with_active_solution(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "web_app.py").write_text("print('x')\n", encoding="utf-8")
    helper = tmp_path / "utils.py"
    helper.write_text("x=1\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._active_solution_file = "web_app.py"
    runner._failing_file = "web_app.py"

    result = execute_tool_with_policy(
        runner,
        "Patch",
        {"unified_diff": "--- a/utils.py\n+++ b/utils.py\n@@ -1 +1 @@\n-x=1\n+x=2\n"},
        "1",
        0,
    )
    assert result["is_error"] is False


def test_recovery_mode_blocks_full_write_of_active_solution_after_hard_failure(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "web_server.py"
    target.write_text("print('x')\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._active_solution_file = "web_server.py"
    runner._failing_file = "web_server.py"
    runner._active_solution_last_validation_ok = False
    runner._file_was_read_since_failure = True

    result = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "web_server.py", "content": "print('rewritten')\n"},
        "1",
        0,
    )
    assert result["is_error"] is True
    assert "full_write_blocked_for_active_solution" in str(result["content"])
    assert '"recovery_blocked": true' in str(result["content"])


def test_recovery_mode_allows_bounded_patch_of_active_solution_after_hard_failure(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "web_server.py"
    target.write_text("x=1\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._active_solution_file = "web_server.py"
    runner._failing_file = "web_server.py"
    runner._active_solution_last_validation_ok = False
    runner._file_was_read_since_failure = True

    result = execute_tool_with_policy(
        runner,
        "Patch",
        {"unified_diff": "--- a/web_server.py\n+++ b/web_server.py\n@@ -1 +1 @@\n-x=1\n+x=2\n"},
        "1",
        0,
    )
    assert result["is_error"] is False


def test_read_failing_file_flips_recovery_read_flag(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "legal_review_app.py").write_text("print('x')\n", encoding="utf-8")
    runner._recovery_mode = True
    runner._failing_file = "legal_review_app.py"
    runner._failing_error_summary = "ImportError"
    runner._file_was_read_since_failure = False

    result = execute_tool_with_policy(
        runner,
        "Read",
        {"file_path": "legal_review_app.py", "max_bytes": 1000},
        "1",
        0,
    )
    assert result["is_error"] is False
    assert runner._file_was_read_since_failure is True
