from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from villani_code.execution_context import TaskExecutionContext
from villani_code.tools import execute_tool


def _paths(ctx: TaskExecutionContext) -> set[str]:
    return set(ctx.attempt.candidate_ledger)


def _ctx(repo: Path) -> TaskExecutionContext:
    context = TaskExecutionContext(repo)
    context.begin_attempt()
    return context


@pytest.mark.skipif(shutil.which("rg") is None, reason="Grep tool requires rg")
def test_grep_records_candidates(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("ordinary-token\n", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("ordinary-token\n", encoding="utf-8")
    context = _ctx(tmp_path)

    result = execute_tool("Grep", {"pattern": "ordinary-token", "path": "."}, tmp_path, execution_context=context)

    assert not result["is_error"]
    assert {"alpha.txt", "beta.txt"}.issubset(_paths(context))


@pytest.mark.skipif(shutil.which("rg") is None, reason="Search tool requires rg")
def test_search_records_candidates(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text("plain marker\n", encoding="utf-8")
    (tmp_path / "two.md").write_text("plain marker\n", encoding="utf-8")
    context = _ctx(tmp_path)

    result = execute_tool("Search", {"query": "plain marker", "path": "."}, tmp_path, execution_context=context)

    assert not result["is_error"]
    assert {"one.md", "two.md"}.issubset(_paths(context))


def test_glob_records_candidates(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "first.data").write_text("a", encoding="utf-8")
    (tmp_path / "src" / "second.data").write_text("b", encoding="utf-8")
    context = _ctx(tmp_path)

    result = execute_tool("Glob", {"pattern": "src/*.data"}, tmp_path, execution_context=context)

    assert not result["is_error"]
    assert {"src/first.data", "src/second.data"}.issubset(_paths(context))


def test_bash_discovery_records_candidates(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "item.txt").write_text("sample", encoding="utf-8")
    context = _ctx(tmp_path)

    result = execute_tool("Bash", {"command": "find folder -name '*.txt'"}, tmp_path, unsafe=True, execution_context=context)

    assert not result["is_error"]
    assert "folder/item.txt" in _paths(context)


def test_read_marks_candidate_inspected(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("content", encoding="utf-8")
    context = _ctx(tmp_path)
    context.attempt.record_candidate_file("note.txt", "test")

    result = execute_tool("Read", {"file_path": "note.txt"}, tmp_path, execution_context=context)

    assert not result["is_error"]
    assert context.attempt.candidate_ledger["note.txt"].status == "inspected"


def test_write_and_patch_mark_candidate_modified(tmp_path: Path) -> None:
    write_target = tmp_path / "write.txt"
    patch_target = tmp_path / "patch.txt"
    write_target.write_text("before", encoding="utf-8")
    patch_target.write_text("old\n", encoding="utf-8")
    context = _ctx(tmp_path)
    context.attempt.record_candidate_file("write.txt", "test")
    context.attempt.record_candidate_file("patch.txt", "test")

    write_result = execute_tool("Write", {"file_path": "write.txt", "content": "after"}, tmp_path, execution_context=context)
    patch_result = execute_tool(
        "Patch",
        {"file_path": "patch.txt", "unified_diff": "--- a/patch.txt\n+++ b/patch.txt\n@@ -1 +1 @@\n-old\n+new\n"},
        tmp_path,
        execution_context=context,
    )

    assert not write_result["is_error"]
    assert not patch_result["is_error"]
    assert context.attempt.candidate_ledger["write.txt"].status == "modified"
    assert context.attempt.candidate_ledger["patch.txt"].status == "modified"


def test_truncated_discovery_warning(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"file_{i}.txt").write_text("sample", encoding="utf-8")
    context = _ctx(tmp_path)

    result = execute_tool("Bash", {"command": "find . -name '*.txt' | head -1"}, tmp_path, unsafe=True, execution_context=context)
    warning = context.attempt.unresolved_candidate_warning()

    assert not result["is_error"]
    assert context.attempt.truncated_discovery_events
    assert "At least one discovery command was truncated or filtered" in warning


def test_path_normalization_coalesces_equivalent_paths(tmp_path: Path) -> None:
    target = tmp_path / "dir" / "same.txt"
    target.parent.mkdir()
    target.write_text("value", encoding="utf-8")
    context = _ctx(tmp_path)

    context.attempt.record_candidate_file(str(target), "absolute")
    context.attempt.record_candidate_file("./dir/same.txt", "dot-relative")
    context.attempt.record_candidate_file("dir/same.txt", "relative")

    assert list(context.attempt.candidate_ledger) == ["dir/same.txt"]


def test_model_visible_warning_is_compact(tmp_path: Path) -> None:
    context = _ctx(tmp_path)
    for i in range(12):
        path = tmp_path / f"generic_{i}.txt"
        path.write_text(str(i), encoding="utf-8")
        context.attempt.record_candidate_file(path, "test")

    warning = context.attempt.unresolved_candidate_warning()
    listed = [line for line in warning.splitlines() if line.startswith("- ")]

    assert len(listed) == 10
    assert len(warning) < 1500


def test_debug_candidate_artifacts_payload(tmp_path: Path) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("marker", encoding="utf-8")
    context = _ctx(tmp_path)
    execute_tool("Glob", {"pattern": "*.txt"}, tmp_path, execution_context=context)
    context.attempt.mark_truncated_discovery("test", "filtered")
    context.attempt.unresolved_candidate_warning()

    payload = context.attempt.to_dict()

    assert payload["candidate_ledger"][0]["path"] == "artifact.txt"
    assert payload["candidate_coverage_summary"]["total"] == 1
    assert payload["truncated_discovery_events"]
    assert payload["unresolved_candidate_warnings"]


def test_runner_emits_candidate_warning_before_finalization(tmp_path: Path) -> None:
    from villani_code.state import Runner

    (tmp_path / "unseen.txt").write_text("common phrase\n", encoding="utf-8")

    class Client:
        def __init__(self) -> None:
            self.calls = 0
            self.payloads: list[dict] = []

        def create_message(self, payload: dict, stream: bool) -> dict:
            del stream
            self.calls += 1
            self.payloads.append(payload)
            if self.calls == 1:
                return {
                    "id": "one",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "grep-one",
                            "name": "Grep",
                            "input": {"pattern": "common phrase", "path": "."},
                        }
                    ],
                }
            return {"id": str(self.calls), "role": "assistant", "content": [{"type": "text", "text": "Finished review."}]}

    client = Client()
    result = Runner(client=client, repo=tmp_path, model="m", stream=False, auto_approve=True).run("review the repository")

    warnings = [
        block.get("text", "")
        for message in client.payloads[-1].get("messages", [])
        if message.get("role") == "user"
        for block in message.get("content", [])
        if isinstance(block, dict)
        and "Search discovered potentially relevant files" in block.get("text", "")
    ]
    assert result["response"]["content"][0]["text"] == "Finished review."
    assert len(warnings) == 2
    assert all("unseen.txt" in warning and len(warning) < 1500 for warning in warnings)
