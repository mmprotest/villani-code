from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WeakSearchPolicyProfile(StrEnum):
    DIRECT_REPAIR_FAST_PATH = "direct_repair_fast_path"
    FAST_PATH_SINGLE_FILE = "fast_path_single_file"
    NORMAL_WEAK_SEARCH = "normal_weak_search"
    ESCALATED_WEAK_SEARCH = "escalated_weak_search"


class RuntimeStrategy(StrEnum):
    DIRECT_REPAIR_FIRST = "direct_repair_first"
    GUIDED_SEARCH_AFTER_FAILURE = "guided_search_after_failure"
    FULL_WEAK_SEARCH = "full_weak_search"


def is_direct_repair_profile(profile: str | WeakSearchPolicyProfile) -> bool:
    normalized = WeakSearchPolicyProfile(str(profile))
    return normalized in {WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH, WeakSearchPolicyProfile.FAST_PATH_SINGLE_FILE}


@dataclass(slots=True)
class PolicyDecision:
    profile: WeakSearchPolicyProfile
    strategy: RuntimeStrategy
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
    task_type: str | None = None,
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
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH,
            strategy=RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE,
            reason="failed_direct_repair_or_no_progress",
        )

    if has_repro and not previous_candidate_failed:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH,
            strategy=RuntimeStrategy.FULL_WEAK_SEARCH,
            reason="repro_signal_requires_broader_diagnosis",
        )

    expected_single_file = len(expected_files) == 1
    normalized_task_family = (task_family or "").strip().lower()
    normalized_task_type = (task_type or "").strip().lower()
    eligible_easy_family = normalized_task_family in {"", "bugfix", "localize_patch"}
    excludes_direct_path = normalized_task_family in {"repro_test", "terminal_workflow"} or "repro" in normalized_task_type
    easy_single_file = (
        bool(getattr(benchmark_config, "enabled", False))
        and expected_single_file
        and max_files_touched <= 1
        and _is_simple_targeted_verification(visible_verification)
        and eligible_easy_family
        and not excludes_direct_path
    )
    if easy_single_file:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH,
            strategy=RuntimeStrategy.DIRECT_REPAIR_FIRST,
            reason="low_ambiguity_local_repair",
        )

    if is_interactive and max_files_touched <= 1 and has_stacktrace_or_error:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH,
            strategy=RuntimeStrategy.DIRECT_REPAIR_FIRST,
            reason="interactive_low_ambiguity_with_local_failure_signal",
        )

    if no_progress_cycles >= 2 and has_stacktrace_or_error:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH,
            strategy=RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE,
            reason="stalled_with_failure_signal",
        )

    if max_files_touched > 1 or normalized_task_family in {"terminal_workflow", "repo_navigation", "repro_test"}:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH,
            strategy=RuntimeStrategy.FULL_WEAK_SEARCH,
            reason="high_ambiguity_or_multi_file_task",
        )

    return PolicyDecision(
        profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH,
        strategy=RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE,
        reason="moderate_ambiguity_default",
    )
