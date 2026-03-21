from __future__ import annotations

from villani_code.benchmark.execution_policy import (
    benchmark_mode_enabled_event,
    benchmark_no_go_paths,
    benchmark_scope_targets,
    benchmark_scope_widening_policy,
    build_benchmark_execution_state,
    has_meaningful_benchmark_edit,
    maybe_block_benchmark_completion,
    maybe_handle_benchmark_prose_only_response,
    note_benchmark_tool_progress,
    observe_benchmark_mutation_denial,
    reset_benchmark_progress_state,
)
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig


def _config() -> BenchmarkRuntimeConfig:
    return BenchmarkRuntimeConfig(
        enabled=True,
        task_id="task-1",
        allowlist_paths=["src/", "tests/"],
        forbidden_paths=["secrets/"],
        expected_files=["src/app.py"],
        allowed_support_files=["tests/test_app.py"],
        max_files_touched=1,
    )


def test_prose_only_after_forced_read_progresses_and_then_terminates() -> None:
    cfg = _config()
    state = build_benchmark_execution_state(cfg, initial_read_enforced=True)

    first = maybe_handle_benchmark_prose_only_response(
        cfg,
        state,
        has_meaningful_edit=False,
        empty_response=False,
        only_textual_response=True,
    )
    second = maybe_handle_benchmark_prose_only_response(
        cfg,
        state,
        has_meaningful_edit=False,
        empty_response=True,
        only_textual_response=False,
    )

    assert state.prose_only_after_forced_read == 2
    assert first.reminder_text == "Benchmark mode: no prose-only turns. Make exactly one concrete next tool call."
    assert first.terminate_reason is None
    assert first.events == [
        {"type": "benchmark_prose_only_after_forced_read", "task_id": "task-1", "attempt": 1}
    ]
    assert second.reminder_text == ""
    assert second.terminate_reason == "benchmark_no_progress_after_forced_read"
    assert second.events[-1] == {
        "type": "benchmark_no_progress_after_forced_read",
        "task_id": "task-1",
    }


def test_tool_progress_disables_forced_read_guard() -> None:
    state = build_benchmark_execution_state(_config(), initial_read_enforced=True)
    note_benchmark_tool_progress(state, tool_uses_present=True)
    assert state.forced_read_no_progress_guard_active is False


def test_noop_completion_blocking_uses_distinct_reminders_and_threshold() -> None:
    cfg = _config()
    state = build_benchmark_execution_state(cfg, initial_read_enforced=False)

    first = maybe_block_benchmark_completion(
        cfg,
        state,
        has_meaningful_edit=False,
        response_kind="empty",
    )
    second = maybe_block_benchmark_completion(
        cfg,
        state,
        has_meaningful_edit=False,
        response_kind="textual_completion",
    )

    assert state.noop_completion_attempts == 2
    assert "actual in-scope patch" in first.reminder_text
    assert first.events[-1] == {
        "type": "benchmark_scope_reminder_injected",
        "task_id": "task-1",
        "reason": "no_meaningful_edit",
    }
    assert second.reminder_text == ""
    assert second.terminate_reason == "benchmark_incomplete_no_patch"
    assert second.events == [
        {"type": "benchmark_noop_completion_blocked", "task_id": "task-1", "attempt": 2}
    ]


def test_repeated_mutation_denials_fast_fail_at_threshold() -> None:
    cfg = _config()
    state = build_benchmark_execution_state(cfg, initial_read_enforced=False)

    for expected_count in (1, 2):
        decision = observe_benchmark_mutation_denial(
            cfg,
            state,
            tool_name="Write",
            result_text="Benchmark policy blocked this mutation. denied.",
            has_meaningful_edit=False,
        )
        assert decision.terminate_reason is None
        assert decision.events == [
            {
                "type": "benchmark_mutation_denial_observed",
                "task_id": "task-1",
                "count": expected_count,
                "limit": 3,
            }
        ]

    final = observe_benchmark_mutation_denial(
        cfg,
        state,
        tool_name="Patch",
        result_text="Benchmark policy blocked this mutation. denied.",
        has_meaningful_edit=False,
    )

    assert state.mutation_denials == 3
    assert final.terminate_reason == "benchmark_repeated_mutation_denials"
    assert final.events[-1] == {
        "type": "benchmark_repeated_mutation_denials",
        "task_id": "task-1",
        "count": 3,
        "limit": 3,
    }


def test_reminder_and_scope_helpers_are_benchmark_owned() -> None:
    cfg = _config()

    assert benchmark_mode_enabled_event(cfg) == {
        "type": "benchmark_mode_enabled",
        "task_id": "task-1",
        "allowlist_paths": ["src/", "tests/"],
        "expected_files": ["src/app.py"],
    }
    assert benchmark_scope_targets(["src/plan.py"], cfg) == [
        "src/plan.py",
        "src/app.py",
        "src/",
        "tests/",
    ]
    assert benchmark_no_go_paths([".git/"], cfg) == [".git/", "secrets/"]


def test_benchmark_state_transitions_reset_after_meaningful_edit() -> None:
    cfg = _config()
    state = build_benchmark_execution_state(cfg, initial_read_enforced=False)
    state.noop_completion_attempts = 2
    state.mutation_denials = 3

    assert has_meaningful_benchmark_edit(cfg, ["src/app.py"]) is True
    assert has_meaningful_benchmark_edit(cfg, ["docs/readme.md"]) is False

    reset_benchmark_progress_state(state)

    assert state.noop_completion_attempts == 0
    assert state.mutation_denials == 0


def test_scope_widening_policy_uses_explicit_benchmark_allowlist() -> None:
    cfg = _config()

    allowlisted = benchmark_scope_widening_policy(cfg, "tests/test_app.py")
    blocked = benchmark_scope_widening_policy(cfg, "docs/readme.md")

    assert allowlisted.explicit_allowlisted is True
    assert allowlisted.blocked_reason is None
    assert blocked.explicit_allowlisted is False
    assert blocked.blocked_reason == "target is outside benchmark allowlist"
