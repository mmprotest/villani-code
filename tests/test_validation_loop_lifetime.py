from __future__ import annotations

import subprocess
from pathlib import Path

from villani_code.project_memory import ValidationStep
from villani_code.validation_loop import (
    ValidationEscalationPolicy,
    ValidationPlan,
    ValidationPlanStep,
    ValidationRunSummary,
    ValidationScope,
    run_validation,
)


def _plan_for_commands(commands: list[str]) -> ValidationPlan:
    steps = [
        ValidationPlanStep(
            step=ValidationStep(
                name=f"step_{idx}",
                command=command,
                kind="test",
                cost_level=1,
                is_mutating=False,
                scope_hint="targeted",
                target_strategy="primary_target",
            ),
            command=command,
            reasons=["test"],
        )
        for idx, command in enumerate(commands)
    ]
    return ValidationPlan(
        scope=ValidationScope([], False, False, False, False, False, [], []),
        selected_steps=steps,
        reasons=[],
        targets=[],
        escalation=ValidationEscalationPolicy(False, False, "test"),
    )


class _FiniteProc:
    def __init__(self) -> None:
        self.returncode = 0

    def communicate(self, timeout: float | None = None):
        return ("ok", "")


class _LongLivedProc:
    def __init__(self) -> None:
        self.returncode = None
        self._timed_out_once = False

    def communicate(self, timeout: float | None = None):
        if not self._timed_out_once:
            self._timed_out_once = True
            raise subprocess.TimeoutExpired(cmd="python run_target.py", timeout=timeout)
        self.returncode = 0
        return ("launched", "")

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def test_finite_direct_run_still_passes_normally(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("villani_code.validation_loop.load_validation_config", lambda _repo: None)
    monkeypatch.setattr("villani_code.validation_loop.load_repo_map", lambda _repo: {})
    monkeypatch.setattr("villani_code.validation_loop.plan_validation", lambda *_args, **_kwargs: _plan_for_commands(["python run_target.py"]))
    monkeypatch.setattr("villani_code.validation_loop.subprocess.Popen", lambda *args, **kwargs: _FiniteProc())

    result = run_validation(tmp_path, ["run_target.py"])

    assert result.passed is True
    assert result.steps[0].run_shape == "finite_run"
    assert result.steps[0].observed_alive_after_window is False


def test_long_lived_direct_run_is_bounded_and_returns_control(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("villani_code.validation_loop.load_validation_config", lambda _repo: None)
    monkeypatch.setattr("villani_code.validation_loop.load_repo_map", lambda _repo: {})
    monkeypatch.setattr("villani_code.validation_loop.plan_validation", lambda *_args, **_kwargs: _plan_for_commands(["python run_target.py"]))
    monkeypatch.setattr("villani_code.validation_loop.subprocess.Popen", lambda *args, **kwargs: _LongLivedProc())

    result = run_validation(tmp_path, ["run_target.py"])

    assert result.passed is False
    assert result.steps[0].run_shape == "long_lived_launch"
    assert result.steps[0].observed_alive_after_window is True
    assert result.steps[0].exit_code == 124


def test_long_lived_launch_proof_stays_conservative(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("villani_code.validation_loop.load_validation_config", lambda _repo: None)
    monkeypatch.setattr("villani_code.validation_loop.load_repo_map", lambda _repo: {})
    monkeypatch.setattr("villani_code.validation_loop.plan_validation", lambda *_args, **_kwargs: _plan_for_commands(["python run_target.py"]))
    monkeypatch.setattr("villani_code.validation_loop.subprocess.Popen", lambda *args, **kwargs: _LongLivedProc())

    result = run_validation(tmp_path, ["run_target.py"])

    assert result.run_summary == ValidationRunSummary(False, ["step_0"], False)
    assert "long-lived-launch" in result.failure_summary


def test_classification_is_generic_not_filename_based(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("villani_code.validation_loop.load_validation_config", lambda _repo: None)
    monkeypatch.setattr("villani_code.validation_loop.load_repo_map", lambda _repo: {})
    monkeypatch.setattr("villani_code.validation_loop.plan_validation", lambda *_args, **_kwargs: _plan_for_commands(["python totally_random_entrypoint.py"]))
    monkeypatch.setattr("villani_code.validation_loop.subprocess.Popen", lambda *args, **kwargs: _LongLivedProc())

    result = run_validation(tmp_path, ["totally_random_entrypoint.py"])

    assert result.steps[0].run_shape == "long_lived_launch"
    assert result.passed is False
