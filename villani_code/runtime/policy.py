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


class AmbiguityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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
    ambiguity_level, ambiguity_reasons = classify_task_ambiguity(
        benchmark_config=benchmark_config,
        is_interactive=is_interactive,
        task_family=task_family,
        task_type=task_type,
        has_stacktrace_or_error=has_stacktrace_or_error,
        objective_text=objective_text,
        failure_text=failure_text,
        previous_candidate_failed=previous_candidate_failed,
        no_progress_cycles=no_progress_cycles,
    )

    if previous_candidate_failed and no_progress_cycles >= 1:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH,
            strategy=RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE,
            reason="failed_direct_repair_or_no_progress",
        )

    if ambiguity_level == AmbiguityLevel.HIGH and not previous_candidate_failed:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH,
            strategy=RuntimeStrategy.FULL_WEAK_SEARCH,
            reason=f"high_ambiguity:{'|'.join(ambiguity_reasons)}",
        )

    if ambiguity_level == AmbiguityLevel.LOW:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH,
            strategy=RuntimeStrategy.DIRECT_REPAIR_FIRST,
            reason=f"low_ambiguity_local_repair:{'|'.join(ambiguity_reasons)}",
        )

    if no_progress_cycles >= 2 and has_stacktrace_or_error:
        return PolicyDecision(
            profile=WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH,
            strategy=RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE,
            reason="stalled_with_failure_signal",
        )

    return PolicyDecision(
        profile=WeakSearchPolicyProfile.ESCALATED_WEAK_SEARCH,
        strategy=RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE,
        reason=f"medium_ambiguity_default:{'|'.join(ambiguity_reasons)}",
    )


def classify_task_ambiguity(
    *,
    benchmark_config: Any,
    is_interactive: bool,
    task_family: str | None,
    task_type: str | None,
    has_stacktrace_or_error: bool,
    objective_text: str = "",
    failure_text: str = "",
    previous_candidate_failed: bool = False,
    no_progress_cycles: int = 0,
) -> tuple[AmbiguityLevel, list[str]]:
    reasons: list[str] = []
    expected_files = [str(f) for f in list(getattr(benchmark_config, "expected_files", []) or [])]
    visible_verification = list(getattr(benchmark_config, "visible_verification", []) or [])
    impl_expected = [p for p in expected_files if p and not p.startswith("tests/")]
    explicit_files = _extract_implementation_paths(objective_text)
    failure_files = _extract_implementation_paths(f"{objective_text}\n{failure_text}".strip())
    bounded_verification = _is_simple_targeted_verification(visible_verification)
    allowlist_impl = [str(p) for p in list(getattr(benchmark_config, "allowlist_paths", []) or []) if str(p) and not str(p).startswith("tests/")]
    bugfix_like = "bug" in (objective_text + " " + failure_text).lower() or "bugfix" in ((task_type or "") + " " + (task_family or "")).lower()
    plausible_target_count = len(set(explicit_files or failure_files or impl_expected or allowlist_impl[:1]))

    normalized_task_family = (task_family or "").strip().lower()
    normalized_task_type = (task_type or "").strip().lower()
    task_id = str(getattr(benchmark_config, "task_id", "") or "").lower()
    if any(token in task_id for token in ("repro", "navigation")) or normalized_task_family in {"repo_navigation", "repro_test", "terminal_workflow"} or normalized_task_type in {"repro", "repo_navigation"}:
        reasons.append("repro_or_navigation_signal")
    if previous_candidate_failed and no_progress_cycles >= 1:
        reasons.append("repeated_failed_attempts")
    if len(set(impl_expected)) > 1 and not explicit_files and len(set(failure_files)) != 1 and not (bugfix_like and plausible_target_count >= 1):
        reasons.append("multiple_plausible_implementation_files")

    if reasons:
        return AmbiguityLevel.HIGH, reasons

    if len(explicit_files) == 1:
        return AmbiguityLevel.LOW, ["objective_identifies_single_implementation_file"]
    if has_stacktrace_or_error and len(set(failure_files)) == 1:
        return AmbiguityLevel.LOW, ["failure_output_identifies_single_implementation_file"]
    if len(impl_expected) == 1:
        return AmbiguityLevel.LOW, ["exactly_one_expected_implementation_file"]
    if bugfix_like and plausible_target_count >= 1:
        return AmbiguityLevel.MEDIUM, ["bugfix_with_plausible_target"]

    if len(set(failure_files)) > 1:
        return AmbiguityLevel.MEDIUM, ["small_candidate_neighborhood_from_failure"]
    if not bounded_verification and not is_interactive:
        return AmbiguityLevel.MEDIUM, ["verification_not_strictly_bounded"]
    return AmbiguityLevel.MEDIUM, ["partial_localization_evidence"]
