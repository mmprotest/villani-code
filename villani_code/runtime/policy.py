from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WeakSearchPolicyProfile(StrEnum):
    DIRECT_REPAIR_FAST_PATH = "direct_repair_fast_path"
    FAST_PATH_SINGLE_FILE = "fast_path_single_file"
    NORMAL_WEAK_SEARCH = "normal_weak_search"
    ESCALATED_WEAK_SEARCH = "escalated_weak_search"


def is_direct_repair_profile(profile: str | WeakSearchPolicyProfile) -> bool:
    normalized = WeakSearchPolicyProfile(str(profile))
    return normalized in {WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH, WeakSearchPolicyProfile.FAST_PATH_SINGLE_FILE}


@dataclass(slots=True)
class PolicyDecision:
    profile: WeakSearchPolicyProfile
    reason: str



def allow_scope_expansion(current_level: str, evidence_score: float) -> tuple[bool, str]:
    if evidence_score < 0.65:
        return False, current_level
    if current_level == "symbol":
        return True, "file"
    if current_level == "file":
        return True, "adjacent_file"
    if current_level == "adjacent_file":
        return True, "two_files"
    return False, current_level



def _is_simple_targeted_verification(commands: list[str]) -> bool:
    if not commands:
        return False
    lowered = " ".join(commands).lower()
    targeted_markers = ("::", "-k ", "tests/", "test_", "repro")
    if any(marker in lowered for marker in targeted_markers):
        return True
    if "unittest" in lowered or "nose" in lowered:
        return True
    return False



def decide_runtime_policy(
    *,
    benchmark_config: Any,
    is_interactive: bool,
    task_family: str | None,
    previous_candidate_failed: bool,
    no_progress_cycles: int,
    has_stacktrace_or_error: bool,
) -> PolicyDecision:
    expected_files = list(getattr(benchmark_config, "expected_files", []) or [])
    max_files_touched = int(getattr(benchmark_config, "max_files_touched", 1) or 1)
    visible_verification = list(getattr(benchmark_config, "visible_verification", []) or [])
    task_id = str(getattr(benchmark_config, "task_id", "") or "")
    has_repro = "repro" in task_id.lower() or any("repro" in command.lower() for command in visible_verification)

    if previous_candidate_failed and (no_progress_cycles >= 1 or max_files_touched > 1):
        return PolicyDecision(profile=WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH, reason="failed_fast_path_or_no_progress")

    if is_interactive:
        return PolicyDecision(profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH, reason="interactive_defaults_to_normal")

    if has_repro:
        return PolicyDecision(profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH, reason="repro_task")

    expected_single_file = len(expected_files) == 1
    normalized_task_family = (task_family or "").strip().lower()
    eligible_easy_family = normalized_task_family in {"", "bugfix", "localize_patch"}
    easy_single_file = (
        bool(getattr(benchmark_config, "enabled", False))
        and expected_single_file
        and max_files_touched <= 1
        and _is_simple_targeted_verification(visible_verification)
        and eligible_easy_family
    )
    if easy_single_file:
        return PolicyDecision(profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH, reason="single_file_benchmark_fast_path")

    if no_progress_cycles >= 2 and has_stacktrace_or_error:
        return PolicyDecision(profile=WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH, reason="stalled_with_failure_signal")

    return PolicyDecision(profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH, reason="default")
