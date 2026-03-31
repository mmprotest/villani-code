from __future__ import annotations

from typing import Any


AUTONOMOUS_VILLANI_PROFILE = "villani_autonomous"


def is_villani_autonomous(runner: Any) -> bool:
    execution_profile = str(getattr(runner, "execution_profile", "default") or "default")
    return execution_profile == AUTONOMOUS_VILLANI_PROFILE


def is_legacy_villani(runner: Any) -> bool:
    return bool(getattr(runner, "villani_mode", False)) and not is_villani_autonomous(runner)


def uses_constrained_tooling_policy(runner: Any) -> bool:
    return (
        bool(getattr(runner, "small_model", False))
        or bool(getattr(getattr(runner, "benchmark_config", None), "enabled", False))
        or is_legacy_villani(runner)
    )


def uses_constrained_runtime_policy(runner: Any) -> bool:
    return (
        bool(getattr(runner, "small_model", False))
        or bool(getattr(getattr(runner, "benchmark_config", None), "enabled", False))
        or is_legacy_villani(runner)
    )


def uses_villani_auto_approval_profile(runner: Any) -> bool:
    return is_legacy_villani(runner)
