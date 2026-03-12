from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
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


_FILE_PATTERN = re.compile(r"\b(?:src|lib|app|villani_code|core|package)/[^\s:'\"]+\.[a-zA-Z0-9_]+\b")


def _extract_implementation_paths(text: str) -> list[str]:
    return [p for p in _FILE_PATTERN.findall(text) if not p.startswith("tests/")]


def is_low_ambiguity_repair(
    *,
    benchmark_config: Any,
    is_interactive: bool,
    task_family: str | None,
    task_type: str | None,
    has_stacktrace_or_error: bool,
    objective_text: str = "",
    failure_text: str = "",
) -> tuple[bool, str]:
    expected_files = [str(f) for f in list(getattr(benchmark_config, "expected_files", []) or [])]
    visible_verification = list(getattr(benchmark_config, "visible_verification", []) or [])
    task_id = str(getattr(benchmark_config, "task_id", "") or "")
    normalized_task_family = (task_family or "").strip().lower()
    normalized_task_type = (task_type or "").strip().lower()
    all_failure_text = f"{objective_text}\n{failure_text}".strip()

    implementation_expected = [p for p in expected_files if p and not p.startswith("tests/")]
    explicit_files = _extract_implementation_paths(objective_text)
    failure_files = _extract_implementation_paths(all_failure_text)

    has_bounded_verification = _is_simple_targeted_verification(visible_verification)
    is_two_stage = "repro" in task_id.lower() or "repro" in normalized_task_type
    inherently_multifile = normalized_task_family in {"terminal_workflow", "repo_navigation", "repro_test"}
    if is_two_stage or inherently_multifile:
        return False, "two_stage_or_inherently_multifile"
    if len(implementation_expected) > 1 and not explicit_files and not failure_files:
        return False, "multiple_implementation_targets"
    if not has_bounded_verification and not is_interactive:
        return False, "unbounded_verification"
    if len(explicit_files) == 1:
        return True, "objective_identifies_single_implementation_file"
    if len(set(failure_files)) == 1 and has_stacktrace_or_error:
        return True, "failure_signal_identifies_single_implementation_file"
    if len(implementation_expected) == 1:
        return True, "expected_single_implementation_file"
    if is_interactive and has_stacktrace_or_error and len(set(failure_files)) == 1:
        return True, "interactive_failure_signal_is_localized"
    return False, "insufficient_localizing_evidence"



def decide_runtime_policy(
    *,
    benchmark_config: Any,
    is_interactive: bool,
    task_family: str | None,
    task_type: str | None = None,
    previous_candidate_failed: bool,
    no_progress_cycles: int,
    has_stacktrace_or_error: bool,
    objective_text: str = "",
    failure_text: str = "",
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

    normalized_task_family = (task_family or "").strip().lower()
    eligible_easy_family = normalized_task_family in {"", "bugfix", "localize_patch"}
    low_ambiguity, low_ambiguity_reason = is_low_ambiguity_repair(
        benchmark_config=benchmark_config,
        is_interactive=is_interactive,
        task_family=task_family,
        task_type=task_type,
        has_stacktrace_or_error=has_stacktrace_or_error,
        objective_text=objective_text,
        failure_text=failure_text,
    )
    if low_ambiguity and eligible_easy_family:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH,
            strategy=RuntimeStrategy.DIRECT_REPAIR_FIRST,
            reason=f"low_ambiguity_local_repair:{low_ambiguity_reason}",
        )

    if no_progress_cycles >= 2 and has_stacktrace_or_error:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH,
            strategy=RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE,
            reason="stalled_with_failure_signal",
        )

    if normalized_task_family in {"terminal_workflow", "repo_navigation", "repro_test"}:
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
