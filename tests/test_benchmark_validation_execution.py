from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from villani_code.benchmark.command_resolution import normalize_command_for_platform
from villani_code.benchmark.graders import execute_validation_checks
from villani_code.benchmark.models import BenchmarkTask


def _task(cmd: str, timeout: int | None = None) -> BenchmarkTask:
    payload = {
        "id": "t",
        "name": "t",
        "instruction": "i",
        "category": "c",
        "validation_checks": [
            {
                "type": "command",
                "command": cmd,
                "expect_exit_code": 0,
                "timeout_seconds": timeout,
            }
        ],
    }
    return BenchmarkTask.model_validate(payload)


def test_normalize_pytest_uses_active_python() -> None:
    resolved = normalize_command_for_platform("pytest -q tests")
    assert resolved.argv[0] == sys.executable
    assert resolved.argv[1:3] == ["-m", "pytest"]


def test_normalize_python_alias_uses_active_python() -> None:
    resolved = normalize_command_for_platform("python -c \"print('ok')\"")
    assert resolved.argv[0] == sys.executable


def test_normalize_unknown_command_is_not_magically_rewritten() -> None:
    resolved = normalize_command_for_platform("definitely-missing-command --version")
    assert resolved.argv[0] == "definitely-missing-command"


def test_normalize_non_allowlisted_module_tool_is_not_rewritten() -> None:
    resolved = normalize_command_for_platform("ruff check .")
    assert resolved.argv[0] == "ruff"


def test_validation_success_classification(tmp_path: Path) -> None:
    results = execute_validation_checks(_task("python -c \"print('ok')\""), tmp_path)
    assert results[0].success is True
    assert results[0].failure_provenance is None


def test_validation_failure_classification(tmp_path: Path) -> None:
    results = execute_validation_checks(_task("python -c \"import sys; sys.exit(1)\""), tmp_path)
    assert results[0].success is False
    assert results[0].failure_provenance == "validation_failure"


def test_validation_environment_failure_classification(monkeypatch, tmp_path: Path) -> None:
    def _boom(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(subprocess, "run", _boom)
    results = execute_validation_checks(_task("python -c \"print('x')\""), tmp_path)
    assert results[0].failure_provenance == "environment_failure"


def test_validation_environment_failure_for_unresolvable_command(tmp_path: Path) -> None:
    results = execute_validation_checks(_task("definitely-missing-command --version"), tmp_path)
    assert results[0].failure_provenance == "environment_failure"
    assert "executable not found" in results[0].details


def test_validation_timeout_classification(monkeypatch, tmp_path: Path) -> None:
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=0.01)

    monkeypatch.setattr(subprocess, "run", _timeout)
    results = execute_validation_checks(_task("python -c \"print('x')\"", timeout=1), tmp_path)
    assert results[0].failure_provenance == "timeout"
