from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from villani_code.execution_context import (
    UNRESOLVED_VALIDATION_MESSAGE,
    VALIDATION_DRIFT_MESSAGE,
    TaskExecutionContext,
    is_weakened_validation_command,
)


def _run_validation(context: TaskExecutionContext, workspace: Path, command: str):
    _completed, record = context.run(command, workspace, 10)
    evidence = context.record_validation(record, kind="command")
    return record, evidence


def test_shell_executor_uses_pipefail_when_bash_is_available(tmp_path: Path) -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    context = TaskExecutionContext(tmp_path)
    context.begin_attempt()

    completed, _record = context.run("false | tail", tmp_path, 10)

    assert completed.returncode != 0


@pytest.mark.parametrize(
    "command",
    ["cmd | head", "cmd | tail", "cmd | grep value", "cmd || true"],
)
def test_filtered_commands_are_marked_as_weak_validation(command: str) -> None:
    assert is_weakened_validation_command(command)


def test_failed_validation_is_retained_at_finalization(tmp_path: Path) -> None:
    context = TaskExecutionContext(tmp_path)
    context.begin_attempt()

    record, _evidence = _run_validation(context, tmp_path, "test -e missing-file")
    context.finish_attempt()
    warnings = context.attempt.finalization_warnings()

    assert record.exit_code != 0
    assert len(context.attempt.unresolved_validation_failures) == 1
    assert UNRESOLVED_VALIDATION_MESSAGE in warnings[0]
    assert "test -e missing-file" in warnings[0]


def test_successful_validation_after_file_change_clears_failure(tmp_path: Path) -> None:
    context = TaskExecutionContext(tmp_path)
    context.begin_attempt()
    _run_validation(context, tmp_path, "test -e marker")

    (tmp_path / "marker").write_text("ready", encoding="utf-8")
    record, _evidence = _run_validation(context, tmp_path, "test -e marker")

    assert record.exit_code == 0
    assert context.attempt.unresolved_validation_failures == []


def test_file_change_after_successful_validation_warns_at_finalization(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    marker.write_text("ready", encoding="utf-8")
    context = TaskExecutionContext(tmp_path)
    context.begin_attempt()
    record, _evidence = _run_validation(context, tmp_path, "test -e marker")
    assert record.exit_code == 0

    marker.write_text("changed", encoding="utf-8")
    context.finish_attempt()

    assert VALIDATION_DRIFT_MESSAGE in context.attempt.finalization_warnings()
