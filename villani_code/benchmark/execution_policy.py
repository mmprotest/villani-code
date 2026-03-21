from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from villani_code.state_types import RunnerBenchmarkConfig


@dataclass(slots=True)
class BenchmarkExecutionState:
    forced_read_no_progress_guard_active: bool = False
    prose_only_after_forced_read: int = 0
    noop_completion_attempts: int = 0
    mutation_denials: int = 0
    mutation_denial_limit: int = 3


@dataclass(slots=True)
class BenchmarkPolicyDecision:
    terminate_reason: str | None = None
    reminder_text: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class BenchmarkScopeWideningPolicy:
    explicit_allowlisted: bool = False
    blocked_reason: str | None = None


def build_benchmark_execution_state(
    benchmark_config: RunnerBenchmarkConfig,
    *,
    initial_read_enforced: bool,
) -> BenchmarkExecutionState:
    if not benchmark_config.enabled:
        return BenchmarkExecutionState()
    return BenchmarkExecutionState(
        forced_read_no_progress_guard_active=initial_read_enforced
    )


def benchmark_mode_enabled_event(
    benchmark_config: RunnerBenchmarkConfig,
) -> dict[str, Any] | None:
    if not benchmark_config.enabled:
        return None
    return {
        "type": "benchmark_mode_enabled",
        "task_id": benchmark_config.task_id,
        "allowlist_paths": benchmark_config.allowlist_paths,
        "expected_files": benchmark_config.expected_files,
    }


def benchmark_scope_targets(
    relevant_files: list[str],
    benchmark_config: RunnerBenchmarkConfig,
) -> list[str]:
    targets = list(relevant_files)
    if benchmark_config.enabled:
        targets.extend(benchmark_config.expected_files)
        targets.extend(benchmark_config.allowlist_paths)
    return targets


def benchmark_no_go_paths(
    base_paths: list[str],
    benchmark_config: RunnerBenchmarkConfig,
) -> list[str]:
    paths = list(base_paths)
    if benchmark_config.enabled:
        paths.extend(benchmark_config.forbidden_paths)
    return paths


def has_meaningful_benchmark_edit(
    benchmark_config: RunnerBenchmarkConfig,
    intentional_changes: list[str],
) -> bool:
    if not benchmark_config.enabled:
        return True
    if not intentional_changes:
        return False
    return any(
        benchmark_config.in_allowlist(path)
        and benchmark_config.is_expected_or_support(path)
        for path in intentional_changes
    )


def note_benchmark_tool_progress(
    state: BenchmarkExecutionState,
    *,
    tool_uses_present: bool,
) -> None:
    if tool_uses_present and state.forced_read_no_progress_guard_active:
        state.forced_read_no_progress_guard_active = False


def maybe_handle_benchmark_prose_only_response(
    benchmark_config: RunnerBenchmarkConfig,
    state: BenchmarkExecutionState,
    *,
    has_meaningful_edit: bool,
    empty_response: bool,
    only_textual_response: bool,
) -> BenchmarkPolicyDecision:
    if (
        not benchmark_config.enabled
        or not state.forced_read_no_progress_guard_active
        or has_meaningful_edit
        or not (empty_response or only_textual_response)
    ):
        return BenchmarkPolicyDecision()

    state.prose_only_after_forced_read += 1
    decision = BenchmarkPolicyDecision(
        events=[
            {
                "type": "benchmark_prose_only_after_forced_read",
                "task_id": benchmark_config.task_id,
                "attempt": state.prose_only_after_forced_read,
            }
        ]
    )
    if state.prose_only_after_forced_read >= 2:
        decision.events.append(
            {
                "type": "benchmark_no_progress_after_forced_read",
                "task_id": benchmark_config.task_id,
            }
        )
        decision.terminate_reason = "benchmark_no_progress_after_forced_read"
        return decision

    decision.reminder_text = (
        "Benchmark mode: no prose-only turns. Make exactly one concrete next tool call."
    )
    return decision


def maybe_block_benchmark_completion(
    benchmark_config: RunnerBenchmarkConfig,
    state: BenchmarkExecutionState,
    *,
    has_meaningful_edit: bool,
    response_kind: Literal["empty", "textual_completion"],
) -> BenchmarkPolicyDecision:
    if (
        not benchmark_config.enabled
        or state.forced_read_no_progress_guard_active
        or has_meaningful_edit
    ):
        return BenchmarkPolicyDecision()

    state.noop_completion_attempts += 1
    reminder = {
        "empty": (
            "Benchmark mode requires an actual in-scope patch. "
            "Edit only expected/allowed support files and continue."
        ),
        "textual_completion": (
            "Benchmark mode requires a real patch in task scope before completion."
        ),
    }[response_kind]
    decision = BenchmarkPolicyDecision(
        events=[
            {
                "type": "benchmark_noop_completion_blocked",
                "task_id": benchmark_config.task_id,
                "attempt": state.noop_completion_attempts,
            }
        ]
    )
    if state.noop_completion_attempts >= 2:
        decision.terminate_reason = "benchmark_incomplete_no_patch"
        return decision

    decision.events.append(
        {
            "type": "benchmark_scope_reminder_injected",
            "task_id": benchmark_config.task_id,
            "reason": "no_meaningful_edit",
        }
    )
    decision.reminder_text = reminder
    return decision


def observe_benchmark_mutation_denial(
    benchmark_config: RunnerBenchmarkConfig,
    state: BenchmarkExecutionState,
    *,
    tool_name: str,
    result_text: str,
    has_meaningful_edit: bool,
) -> BenchmarkPolicyDecision:
    if (
        not benchmark_config.enabled
        or tool_name not in {"Write", "Patch"}
        or "Benchmark policy blocked this mutation" not in result_text
    ):
        return BenchmarkPolicyDecision()

    state.mutation_denials += 1
    decision = BenchmarkPolicyDecision(
        events=[
            {
                "type": "benchmark_mutation_denial_observed",
                "task_id": benchmark_config.task_id,
                "count": state.mutation_denials,
                "limit": state.mutation_denial_limit,
            }
        ]
    )
    if (
        state.mutation_denials >= state.mutation_denial_limit
        and not has_meaningful_edit
    ):
        decision.events.append(
            {
                "type": "benchmark_repeated_mutation_denials",
                "task_id": benchmark_config.task_id,
                "count": state.mutation_denials,
                "limit": state.mutation_denial_limit,
            }
        )
        decision.terminate_reason = "benchmark_repeated_mutation_denials"
    return decision


def reset_benchmark_progress_state(state: BenchmarkExecutionState) -> None:
    state.noop_completion_attempts = 0
    state.mutation_denials = 0


def benchmark_scope_widening_policy(
    benchmark_config: RunnerBenchmarkConfig,
    file_path: str,
) -> BenchmarkScopeWideningPolicy:
    if not benchmark_config.enabled:
        return BenchmarkScopeWideningPolicy()
    if benchmark_config.in_allowlist(file_path):
        return BenchmarkScopeWideningPolicy(explicit_allowlisted=True)
    return BenchmarkScopeWideningPolicy(
        blocked_reason="target is outside benchmark allowlist"
    )
