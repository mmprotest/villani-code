from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from villani_code.execution_policy import (
    is_legacy_villani,
    is_villani_autonomous,
    uses_constrained_runtime_policy,
    uses_constrained_tooling_policy,
    uses_villani_auto_approval_profile,
)
from villani_code.state import Runner


class _Client:
    def create_message(self, _payload, stream):
        return {"content": [{"type": "text", "text": "ok"}]}


def _runner_like(*, villani_mode: bool, execution_profile: str, small_model: bool = False, benchmark: bool = False):
    return SimpleNamespace(
        villani_mode=villani_mode,
        execution_profile=execution_profile,
        small_model=small_model,
        benchmark_config=SimpleNamespace(enabled=benchmark),
    )


def test_execution_policy_helpers_semantic_split() -> None:
    autonomous = _runner_like(villani_mode=True, execution_profile="villani_autonomous")
    assert is_villani_autonomous(autonomous) is True
    assert is_legacy_villani(autonomous) is False
    assert uses_constrained_tooling_policy(autonomous) is False
    assert uses_constrained_runtime_policy(autonomous) is False
    assert uses_villani_auto_approval_profile(autonomous) is False

    legacy = _runner_like(villani_mode=True, execution_profile="default")
    assert is_villani_autonomous(legacy) is False
    assert is_legacy_villani(legacy) is True
    assert uses_constrained_tooling_policy(legacy) is True
    assert uses_constrained_runtime_policy(legacy) is True
    assert uses_villani_auto_approval_profile(legacy) is True

    non_villani = _runner_like(villani_mode=False, execution_profile="default")
    assert is_villani_autonomous(non_villani) is False
    assert is_legacy_villani(non_villani) is False
    assert uses_constrained_tooling_policy(non_villani) is False
    assert uses_constrained_runtime_policy(non_villani) is False
    assert uses_villani_auto_approval_profile(non_villani) is False

    non_villani_autonomous_profile = _runner_like(villani_mode=False, execution_profile="villani_autonomous")
    assert is_villani_autonomous(non_villani_autonomous_profile) is False
    assert is_legacy_villani(non_villani_autonomous_profile) is False
    assert uses_constrained_tooling_policy(non_villani_autonomous_profile) is False
    assert uses_constrained_runtime_policy(non_villani_autonomous_profile) is False
    assert uses_villani_auto_approval_profile(non_villani_autonomous_profile) is False


def test_execution_policy_helpers_keep_small_model_and_benchmark_constraints() -> None:
    small_model = _runner_like(villani_mode=False, execution_profile="default", small_model=True)
    assert uses_constrained_tooling_policy(small_model) is True
    assert uses_constrained_runtime_policy(small_model) is True

    benchmark = _runner_like(villani_mode=False, execution_profile="default", benchmark=True)
    assert uses_constrained_tooling_policy(benchmark) is True
    assert uses_constrained_runtime_policy(benchmark) is True


def test_runner_methods_delegate_to_execution_policy(tmp_path: Path) -> None:
    runner = Runner(client=_Client(), repo=tmp_path, model="m", stream=False, villani_mode=True)
    runner.execution_profile = "villani_autonomous"
    assert runner.is_villani_autonomous_execution() is True
    assert runner.is_legacy_villani() is False
    assert runner.uses_constrained_tooling_policy() is False
    assert runner.uses_constrained_runtime_policy() is False
    assert runner.uses_villani_auto_approval_profile() is False

    runner.execution_profile = "default"
    assert runner.is_villani_autonomous_execution() is False
    assert runner.is_legacy_villani() is True
    assert runner.uses_constrained_tooling_policy() is True
    assert runner.uses_constrained_runtime_policy() is True
    assert runner.uses_villani_auto_approval_profile() is True
