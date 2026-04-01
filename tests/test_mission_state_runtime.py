from __future__ import annotations

import json
from pathlib import Path

from villani_code.context_projection import build_model_context_packet
from villani_code.debug_bundle import create_debug_bundle
from villani_code.event_recorder import RuntimeEventRecorder
from villani_code.mission_state import (
    MissionState,
    OpenHypothesis,
    VerifiedFact,
    create_mission_state,
    get_current_mission_id,
    get_current_mission_metadata_path,
    get_mission_dir,
    load_mission_state,
    save_mission_state,
)
from villani_code.plan_session import PlanAnswer, PlanOption, PlanQuestion, PlanSessionResult
from villani_code.state import Runner
from villani_code.state_runtime import save_session_snapshot


class DummyClient:
    def create_message(self, payload, stream=True):
        return {"content": []}


def test_mission_state_roundtrip(tmp_path: Path) -> None:
    state = MissionState(
        mission_id="m1",
        objective="obj",
        mode="execution",
        repo_root=str(tmp_path),
        status="active",
        verified_facts=[VerifiedFact(kind="k", value="v", source="s")],
        open_hypotheses=[OpenHypothesis(hypothesis_id="h1", statement="maybe", confidence=0.4, status="open")],
    )
    save_mission_state(tmp_path, state)
    loaded = load_mission_state(tmp_path, "m1")
    assert loaded.verified_facts[0].value == "v"
    assert loaded.open_hypotheses[0].hypothesis_id == "h1"


def test_mission_directory_and_current_pointer(tmp_path: Path) -> None:
    state = create_mission_state(tmp_path, "ship", "execution", mission_id="m2")
    assert get_mission_dir(tmp_path, state.mission_id).exists()
    assert get_current_mission_metadata_path(tmp_path).exists()
    assert get_current_mission_id(tmp_path) == "m2"


def test_save_session_snapshot_writes_mission_artifacts(tmp_path: Path) -> None:
    state = create_mission_state(tmp_path, "ship", "execution", mission_id="m3")
    mission_dir = get_mission_dir(tmp_path, "m3")
    recorder = RuntimeEventRecorder(mission_dir)

    class RunnerStub:
        repo = tmp_path
        model = "m"
        _mission_state = state
        _mission_dir = mission_dir
        _event_recorder = recorder

    save_session_snapshot(RunnerStub(), [{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
    assert (mission_dir / "messages.json").exists()
    assert (mission_dir / "working_summary.md").exists()
    assert (mission_dir / "event_digest.json").exists()
    assert (tmp_path / ".villani_code" / "sessions" / "last.json").exists()


def test_transcript_save_updates_mission_state(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="x", stream=False, print_stream=False)
    runner._ensure_mission("objective")
    path = runner._save_transcript_and_link({"requests": [], "responses": []})
    loaded = load_mission_state(tmp_path, runner._mission_id)
    assert loaded.last_transcript_path == str(path)


def test_plan_artifact_serialization_roundtrip() -> None:
    plan = PlanSessionResult(
        instruction="i",
        task_summary="s",
        candidate_files=["a.py"],
        assumptions=["x"],
        recommended_steps=["do"],
        open_questions=[PlanQuestion(id="q", question="q", rationale="r", options=[PlanOption("a","A","",False),PlanOption("b","B","",False),PlanOption("c","C","",False),PlanOption("other","Other","",True)])],
        resolved_answers=[PlanAnswer(question_id="q", selected_option_id="a")],
        ready_to_execute=False,
    )
    loaded = PlanSessionResult.from_dict(plan.to_dict())
    assert loaded.instruction == "i"
    assert loaded.open_questions[0].id == "q"


def test_event_recorder_jsonl_and_digest(tmp_path: Path) -> None:
    mission_dir = get_mission_dir(tmp_path, "m4")
    recorder = RuntimeEventRecorder(mission_dir)
    recorder.record({"type": "tool_result", "name": "Read", "is_error": False})
    recorder.record({"type": "validation_started"})
    digest = recorder.build_digest()
    assert digest["total_events"] == 2
    assert (mission_dir / "runtime_events.jsonl").exists()


def test_debug_bundle_contains_expected_files(tmp_path: Path) -> None:
    state = create_mission_state(tmp_path, "ship", "execution", mission_id="m5")
    mission_dir = get_mission_dir(tmp_path, "m5")
    (mission_dir / "messages.json").write_text("[]", encoding="utf-8")
    (mission_dir / "event_digest.json").write_text("{}", encoding="utf-8")
    (mission_dir / "working_summary.md").write_text("summary", encoding="utf-8")
    save_mission_state(tmp_path, state)
    bundle = create_debug_bundle(tmp_path, mission_id="m5")
    assert bundle.exists()


def test_context_projection_packet(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="x", stream=False, print_stream=False)
    runner._ensure_mission("do x")
    packet = build_model_context_packet(runner)
    assert packet["objective"] == "do x"


def test_context_projection_excludes_runtime_artifacts(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="x", stream=False, print_stream=False)
    runner._ensure_mission("do x")
    runner._mission_state.changed_files = ["src/app.py", ".villani_code/missions/m1/state.json"]
    runner._mission_state.intended_targets = ["./.villani_code/sessions/last.json", "tests/test_app.py"]
    packet = build_model_context_packet(runner)
    assert packet["changed_files"] == ["src/app.py"]
    assert packet["intended_targets"] == ["tests/test_app.py"]


def test_projected_context_not_injected_on_initial_turn_even_when_requested(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="x", stream=False, print_stream=False)
    runner._ensure_mission("objective")
    messages = [{"role": "user", "content": [{"type": "text", "text": "task prompt"}]}]
    runner._inject_projected_context(messages)
    assert len(messages) == 1
    assert messages[0]["content"][0]["text"] == "task prompt"


def test_projected_context_not_injected_in_benchmark_mode(tmp_path: Path) -> None:
    from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig

    runner = Runner(
        client=DummyClient(),
        repo=tmp_path,
        model="x",
        stream=False,
        print_stream=False,
        benchmark_config=BenchmarkRuntimeConfig(enabled=True, task_id="t1"),
    )
    runner._ensure_mission("objective")
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "older context"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ack"}]},
        {"role": "user", "content": [{"type": "text", "text": "benchmark contract prompt"}]},
    ]
    runner._inject_projected_context(messages)
    assert messages[-1]["content"][0]["text"] == "benchmark contract prompt"
    assert not any(
        "Mission context packet:" in str(block.get("text", ""))
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict)
    )


def test_compaction_survival_guidance() -> None:
    from villani_code.context_governance import ContextCompactor

    summary = ContextCompactor.build_compact_mission_summary({"active_skill_guidance": ["always verify", "keep scope narrow"]})
    assert "always verify" in summary


def test_autonomous_summary_mirrors_to_mission_state(tmp_path: Path, monkeypatch) -> None:
    class FakeController:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            return {
                "waves": 2,
                "attempted": [{"id": "a"}],
                "done_reason": "done",
                "recommended_next_steps": ["n1"],
                "working_memory": {
                    "satisfied_task_keys": {"k": "v"},
                    "stop_decision_rationale": {"x": "blocked"},
                },
            }

        @staticmethod
        def format_summary(summary):
            return "ok"

    monkeypatch.setattr("villani_code.state.VillaniModeController", FakeController)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="x", stream=False, print_stream=False, villani_mode=True)
    runner.run_villani_mode()
    state = load_mission_state(tmp_path, runner._mission_id)
    assert state.mode == "autonomous"
    assert state.autonomous_wave == 2
