from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy
from villani_code.workspace_snapshot import diff_workspace_snapshots, snapshot_workspace


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


def _runner(tmp_path: Path, events: list[dict[str, Any]] | None = None) -> Runner:
    runner = Runner(
        client=_Client(),
        repo=tmp_path,
        model="m",
        stream=False,
        plan_mode="off",
        event_callback=(events.append if events is not None else None),
    )
    runner.hooks = _Hooks()
    runner.permissions = _PermissivePermissions()
    return runner


def _run_bash(runner: Runner, command: str) -> dict[str, Any]:
    return execute_tool_with_policy(runner, "Bash", {"command": command}, "bash-1", 0)


def test_bash_no_mutation_does_not_increment_workspace_revision(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    events: list[dict[str, Any]] = []
    runner = _runner(tmp_path, events)

    result = _run_bash(runner, "true")

    assert result["is_error"] is False
    assert runner.workspace_revision == 0
    assert any(event["type"] == "workspace_snapshot_before_bash" for event in events)
    assert any(event["type"] == "workspace_snapshot_after_bash" for event in events)
    assert not any(event["type"] == "workspace_revision_incremented" for event in events)


def test_bash_file_size_mutation_increments_workspace_revision(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    runner = _runner(tmp_path)

    result = _run_bash(runner, "printf 'longer\\n' > a.txt")

    assert result["is_error"] is False
    assert runner.workspace_revision == 1


def test_bash_mtime_only_mutation_increments_workspace_revision_when_available(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("same-size\n", encoding="utf-8")
    before_ns = target.stat().st_mtime_ns
    runner = _runner(tmp_path)

    result = _run_bash(runner, f"python -c \"import os; os.utime('a.txt', ns=({before_ns}, {before_ns + 1_000_000_000}))\"")

    assert result["is_error"] is False
    if getattr(target.stat(), "st_mtime_ns", None) is not None:
        assert runner.workspace_revision == 1


def test_bash_file_added_increments_workspace_revision(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    runner = _runner(tmp_path)

    result = _run_bash(runner, "printf 'new\\n' > b.txt")

    assert result["is_error"] is False
    assert runner.workspace_revision == 1


def test_bash_file_removed_increments_workspace_revision(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    runner = _runner(tmp_path)

    result = _run_bash(runner, "rm a.txt")

    assert result["is_error"] is False
    assert runner.workspace_revision == 1


def test_ignored_directory_changes_do_not_increment_workspace_revision(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    for ignored in [".git", ".venv", "node_modules", "__pycache__"]:
        ignored_path = tmp_path / ignored
        ignored_path.mkdir()
        runner = _runner(tmp_path)

        result = _run_bash(runner, f"printf 'ignored\\n' > {ignored}/generated.txt")

        assert result["is_error"] is False
        assert runner.workspace_revision == 0


def test_workspace_snapshot_truncation_is_flagged(tmp_path: Path) -> None:
    for idx in range(3):
        (tmp_path / f"{idx}.txt").write_text(str(idx), encoding="utf-8")

    snapshot = snapshot_workspace(tmp_path, max_files=2)

    assert snapshot.truncated is True
    assert snapshot.scanned_files == 2


def test_workspace_snapshot_diff_reports_added_removed_and_modified(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "remove.txt").write_text("remove", encoding="utf-8")
    before = snapshot_workspace(tmp_path)
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    (tmp_path / "remove.txt").unlink()
    (tmp_path / "add.txt").write_text("add", encoding="utf-8")

    diff = diff_workspace_snapshots(before, snapshot_workspace(tmp_path))

    assert diff.added == 1
    assert diff.removed == 1
    assert diff.modified == 1
    assert diff.changed is True


def test_direct_write_increments_workspace_revision_once(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    result = execute_tool_with_policy(runner, "Write", {"file_path": "a.txt", "content": "x\n"}, "write-1", 0)

    assert result["is_error"] is False
    assert runner.workspace_revision == 1


def test_direct_patch_increments_workspace_revision_once(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    runner = _runner(tmp_path)
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"

    result = execute_tool_with_policy(runner, "Patch", {"unified_diff": diff}, "patch-1", 0)

    assert result["is_error"] is False
    assert runner.workspace_revision == 1
