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


def test_regular_runner_generic_no_tool_reply_with_unresolved_verification_does_not_complete(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "text", "text": "What would you like me to help you with?"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "How would you like to proceed?"}]},
        ],
    )

    result = runner.run("repair failing tests")

    assert result["execution"]["completed"] is False
    assert result["execution"]["terminated_reason"] == "stalled_context_loss"
