import json
from pathlib import Path

from villani_code.tools import BashInput, MASKED_FAILURE_WARNING, _run_bash


def _run(command: str, tmp_path: Path) -> dict[str, object]:
    out = _run_bash(BashInput(command=command), repo=tmp_path, unsafe=True)
    return json.loads(out)


def test_warning_appended_for_or_echo_when_exit_zero(tmp_path: Path) -> None:
    result = _run("false || echo recovered", tmp_path)
    assert result["exit_code"] == 0
    assert result["note"] == MASKED_FAILURE_WARNING


def test_warning_appended_for_or_true_when_exit_zero(tmp_path: Path) -> None:
    result = _run("false || true", tmp_path)
    assert result["exit_code"] == 0
    assert result["note"] == MASKED_FAILURE_WARNING


def test_non_zero_exit_does_not_append_warning(tmp_path: Path) -> None:
    result = _run("false || echo recovered; exit 3", tmp_path)
    assert result["exit_code"] == 3
    assert "note" not in result


def test_exit_zero_without_patterns_does_not_append_warning(tmp_path: Path) -> None:
    result = _run("echo ok", tmp_path)
    assert result["exit_code"] == 0
    assert "note" not in result


def test_warning_does_not_change_success_status(tmp_path: Path) -> None:
    result = _run("false || true", tmp_path)
    assert result["exit_code"] == 0
