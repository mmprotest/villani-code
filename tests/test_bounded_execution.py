from __future__ import annotations

from pathlib import Path

from villani_code.execution import ExecutionBudget
from villani_code.state import Runner


class SequenceClient:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0

    def create_message(self, payload, stream):
        out = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return out


def _runner(tmp_path: Path, responses: list[dict]) -> Runner:
    return Runner(client=SequenceClient(responses), repo=tmp_path, model="m", stream=False)


def _default_budget() -> ExecutionBudget:
    return ExecutionBudget(max_turns=20, max_tool_calls=20, max_seconds=100.0, max_no_edit_turns=20, max_reconsecutive_recon_turns=20)


def test_villani_task_stops_on_max_turns(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Read", "input": {"file_path": "a.txt"}}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "2", "name": "Read", "input": {"file_path": "a.txt"}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "still investigating"}]},
        ],
    )
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    budget = ExecutionBudget(max_turns=2, max_tool_calls=10, max_seconds=100.0, max_no_edit_turns=20, max_reconsecutive_recon_turns=20)

    result = runner.run("work", execution_budget=budget)

    assert result["execution"]["terminated_reason"] == "max_turns"


def test_villani_task_stops_on_max_tool_calls(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "1", "name": "Ls", "input": {"path": "."}},
                    {"type": "tool_use", "id": "2", "name": "Ls", "input": {"path": "."}},
                ],
            }
        ],
    )
    budget = ExecutionBudget(max_turns=20, max_tool_calls=2, max_seconds=100.0, max_no_edit_turns=20, max_reconsecutive_recon_turns=20)

    result = runner.run("work", execution_budget=budget)

    assert result["execution"]["terminated_reason"] == "max_tool_calls"


def test_villani_task_stops_on_max_seconds(tmp_path: Path, monkeypatch) -> None:
    runner = _runner(tmp_path, [{"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Ls", "input": {"path": "."}}]}])
    ticks = iter([0.0, 0.2, 0.4, 2.0, 2.1])
    monkeypatch.setattr("villani_code.state.time.monotonic", lambda: next(ticks))
    budget = ExecutionBudget(max_turns=20, max_tool_calls=20, max_seconds=1.0, max_no_edit_turns=20, max_reconsecutive_recon_turns=20)

    result = runner.run("work", execution_budget=budget)

    assert result["execution"]["terminated_reason"] == "max_seconds"


def test_villani_task_stops_on_recon_loop(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Ls", "input": {"path": "."}}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "2", "name": "Read", "input": {"file_path": "a.txt"}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ],
    )
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    budget = ExecutionBudget(max_turns=20, max_tool_calls=20, max_seconds=100.0, max_no_edit_turns=20, max_reconsecutive_recon_turns=2)

    result = runner.run("work", execution_budget=budget)

    assert result["execution"]["terminated_reason"] == "recon_loop"


def test_villani_task_stops_on_no_edits(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Ls", "input": {"path": "."}}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "2", "name": "Search", "input": {"query": "x", "path": "."}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "keep going"}]},
        ],
    )
    budget = ExecutionBudget(max_turns=20, max_tool_calls=20, max_seconds=100.0, max_no_edit_turns=2, max_reconsecutive_recon_turns=10)

    result = runner.run("work", execution_budget=budget)

    assert result["execution"]["terminated_reason"] == "no_edits"


def test_villani_task_reports_completed_when_done(tmp_path: Path) -> None:
    runner = _runner(tmp_path, [{"role": "assistant", "content": [{"type": "text", "text": "all done"}]}])
    budget = ExecutionBudget(max_turns=20, max_tool_calls=20, max_seconds=100.0, max_no_edit_turns=20, max_reconsecutive_recon_turns=20)

    result = runner.run("work", execution_budget=budget)

    assert result["execution"]["completed"] is True
    assert result["execution"]["terminated_reason"] == "completed"


def test_fix_request_prose_only_edit_intent_continues(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "text", "text": "Found the bug. Here's the fix:"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "no code change is needed"}]},
        ],
    )
    result = runner.run("Fix the bug", execution_budget=_default_budget())
    assert any(
        "did not edit the repository" in block.get("text", "")
        for message in result["messages"]
        if message.get("role") == "user"
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )


def test_fix_request_prose_only_change_x_to_y_continues(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "text", "text": "The fix is to change X to Y"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "no code change is needed"}]},
        ],
    )
    result = runner.run("Fix the bug", execution_budget=_default_budget())
    assert result["response"]["content"][0]["text"] == "no code change is needed"


def test_after_two_recovery_attempts_returns_incomplete(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [{"role": "assistant", "content": [{"type": "text", "text": "Found the bug. Here's the fix:"}]}],
    )
    result = runner.run("Fix the bug")
    assert result["execution"]["terminated_reason"] == "incomplete_no_patch_after_edit_intent"
    assert result["execution"]["completed"] is False


def test_meaningful_write_allows_final_prose_completion(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "src/app.py", "content": "print('ok')\n"}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Implemented the fix."}]},
        ],
    )
    result = runner.run("Fix the bug", execution_budget=_default_budget())
    assert result["execution"]["terminated_reason"] == "completed"


def test_artifact_only_write_does_not_count_as_meaningful_edit(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "dist/build.log", "content": "x"}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "The fix is to change X to Y."}]},
            {"role": "assistant", "content": [{"type": "text", "text": "no code change is needed"}]},
        ],
    )
    result = runner.run("Fix the bug")
    assert any(
        "did not edit the repository" in block.get("text", "")
        for message in result["messages"]
        if message.get("role") == "user"
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )


def test_explain_request_allows_prose_only_completion(tmp_path: Path) -> None:
    runner = _runner(tmp_path, [{"role": "assistant", "content": [{"type": "text", "text": "Explanation only."}]}])
    result = runner.run("Explain this bug", execution_budget=_default_budget())
    assert result["execution"]["terminated_reason"] == "completed"


def test_review_request_allows_prose_only_completion(tmp_path: Path) -> None:
    runner = _runner(tmp_path, [{"role": "assistant", "content": [{"type": "text", "text": "Review complete."}]}])
    result = runner.run("Review this file", execution_budget=_default_budget())
    assert result["execution"]["terminated_reason"] == "completed"


def test_find_where_dont_edit_allows_prose_only_completion(tmp_path: Path) -> None:
    runner = _runner(tmp_path, [{"role": "assistant", "content": [{"type": "text", "text": "The bug is in src/x.py."}]}])
    result = runner.run("Find where the bug is, don't edit", execution_budget=_default_budget())
    assert result["execution"]["terminated_reason"] == "completed"


def test_explicit_no_change_needed_allows_prose_only_completion(tmp_path: Path) -> None:
    runner = _runner(tmp_path, [{"role": "assistant", "content": [{"type": "text", "text": "No code change is needed because behavior matches spec."}]}])
    result = runner.run("Fix the bug", execution_budget=_default_budget())
    assert result["execution"]["terminated_reason"] == "completed"
