from __future__ import annotations

from villani_code.runtime_events import RuntimeEvent, RuntimeEventChannel, RuntimeEventType


def test_runtime_event_maps_transcript_event_types() -> None:
    stream = RuntimeEvent.from_runner_event({"type": "stream_text", "text": "hello"})
    assert stream is not None
    assert stream.event_type is RuntimeEventType.STREAM_CHUNK
    assert stream.channel is RuntimeEventChannel.TRANSCRIPT
    assert stream.durable is True

    tool = RuntimeEvent.from_runner_event({"type": "tool_started", "name": "Read"})
    assert tool is not None
    assert tool.event_type is RuntimeEventType.TOOL_ACTIVITY
    assert tool.channel is RuntimeEventChannel.TRANSCRIPT


def test_runtime_event_maps_approval_and_autonomous_completion() -> None:
    approval = RuntimeEvent.from_runner_event({"type": "approval_requested"})
    assert approval is not None
    assert approval.event_type is RuntimeEventType.APPROVAL_REQUESTED
    assert approval.channel is RuntimeEventChannel.APPROVAL
    assert approval.durable is True

    done = RuntimeEvent.from_runner_event({"type": "villani_stop_decision", "done_reason": "done"})
    assert done is not None
    assert done.event_type is RuntimeEventType.AUTONOMOUS_COMPLETION
    assert done.channel is RuntimeEventChannel.AUTONOMOUS
    assert done.message == "done"


def test_runtime_event_maps_benchmark_lifecycle_and_status() -> None:
    benchmark = RuntimeEvent.from_runner_event({"type": "benchmark_run_started"})
    assert benchmark is not None
    assert benchmark.event_type is RuntimeEventType.BENCHMARK_LIFECYCLE
    assert benchmark.channel is RuntimeEventChannel.BENCHMARK

    status = RuntimeEvent.from_runner_event({"type": "planning_started"})
    assert status is not None
    assert status.event_type is RuntimeEventType.STATUS
    assert status.channel is RuntimeEventChannel.STATUS
    assert status.durable is False


def test_runtime_event_maps_plan_and_checkpoint_durable_events() -> None:
    plan = RuntimeEvent.from_runner_event({"type": "plan_generated"})
    assert plan is not None
    assert plan.durable is True

    checkpoint = RuntimeEvent.from_runner_event({"type": "checkpoint_created", "checkpoint_id": "c1"})
    assert checkpoint is not None
    assert checkpoint.durable is True
