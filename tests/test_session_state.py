from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from villani_code.focus_block import render_focus_block
from villani_code.session_state import SessionMemory, load_session_state, update_session_state
from villani_code.state import Runner
from villani_code import state_runtime


def _seed_repo(repo: Path) -> None:
    (repo / "villani_code").mkdir(parents=True, exist_ok=True)
    (repo / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


def test_load_session_state_when_missing_returns_defaults(tmp_path: Path) -> None:
    state = load_session_state(tmp_path)

    assert state.current_goal == ""
    assert state.recent_actions == []


def test_load_session_state_survives_corrupted_json(tmp_path: Path) -> None:
    session_path = tmp_path / ".villani" / "session_state.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{not-json", encoding="utf-8")

    state = load_session_state(tmp_path)

    assert state.current_goal == ""
    assert state.failed_hypotheses == []


def test_save_and_reload_session_state_round_trips(tmp_path: Path) -> None:
    saved = update_session_state(
        tmp_path,
        SessionMemory(
            current_goal="Fix context continuity",
            current_plan=["Inspect state runtime", "Add focus block"],
            last_command="pytest -q",
            last_command_result="1 failed",
            changed_files=["villani_code/state.py"],
            next_action="Patch the prompt assembly path",
        ),
    )

    loaded = load_session_state(tmp_path)

    assert saved.current_goal == "Fix context continuity"
    assert loaded.current_plan == ["Inspect state runtime", "Add focus block"]
    assert loaded.last_command == "pytest -q"
    assert loaded.changed_files == ["villani_code/state.py"]
    assert loaded.updated_at


def test_recent_actions_are_capped(tmp_path: Path) -> None:
    for idx in range(20):
        update_session_state(tmp_path, SessionMemory(recent_actions=[f"action {idx}"]))

    loaded = load_session_state(tmp_path)

    assert len(loaded.recent_actions) == 12
    assert loaded.recent_actions[0] == "action 8"
    assert loaded.recent_actions[-1] == "action 19"


def test_focus_block_omits_empty_fields_and_stays_compact() -> None:
    block = render_focus_block(
        SessionMemory(
            current_goal="Keep follow-up turns anchored",
            current_plan=["Load state", "Render focus", "Save turn updates"],
            last_command="pytest tests/test_session_state.py -q",
            changed_files=["villani_code/state.py", "villani_code/state_runtime.py"],
            next_action="Run the focused tests again",
        ),
        max_chars=320,
    )

    assert "[FOCUS]" in block
    assert "Latest error:" not in block
    assert "Failed ideas:" not in block
    assert len(block) <= 320


def test_persisted_schema_stays_minimal(tmp_path: Path) -> None:
    update_session_state(
        tmp_path,
        SessionMemory(
            current_goal="Keep continuity",
            current_plan=["Load saved state"],
            recent_actions=["Read villani_code/state.py"],
        ),
    )

    payload = json.loads((tmp_path / ".villani" / "session_state.json").read_text(encoding="utf-8"))

    assert sorted(payload.keys()) == [
        "attempted_fixes",
        "changed_files",
        "current_goal",
        "current_plan",
        "failed_hypotheses",
        "last_command",
        "last_command_result",
        "latest_error",
        "next_action",
        "recent_actions",
        "updated_at",
    ]


def test_sync_session_state_keeps_runner_cache_populated(tmp_path: Path) -> None:
    runner = SimpleNamespace(repo=tmp_path, _session_state=None)

    synced = state_runtime._sync_session_state(  # noqa: SLF001
        runner,
        SessionMemory(current_goal="Keep session cache alive"),
    )

    assert runner._session_state is synced
    assert runner._session_state.current_goal == "Keep session cache alive"


def test_focus_block_integration_uses_runtime_system_context_without_mutating_history() -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "Continue the fix"}]}]
    system_blocks = [{"type": "text", "text": "base system"}]
    runner = SimpleNamespace(
        repo=Path("."),
        _session_state=SessionMemory(current_goal="Remember the previous turn"),
        _focus_session_state=SessionMemory(current_goal="Remember the previous turn"),
    )

    prepared_system = state_runtime.prepare_system_blocks_for_model(runner, system_blocks)

    assert prepared_system[0] == system_blocks[0]
    assert "<runtime-context>" in prepared_system[1]["text"]
    assert "[FOCUS]" in prepared_system[1]["text"]
    assert messages == [{"role": "user", "content": [{"type": "text", "text": "Continue the fix"}]}]
    assert system_blocks == [{"type": "text", "text": "base system"}]


class _ContinuityClient:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def create_message(self, payload, stream):
        self.payloads.append(payload)
        return {
            "id": str(len(self.payloads)),
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
        }


def test_second_turn_sees_state_from_first_turn(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    client = _ContinuityClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    runner.run("Add compact session memory")
    runner.run("Continue the same improvement")

    first_state = json.loads((tmp_path / ".villani" / "session_state.json").read_text(encoding="utf-8"))
    second_system_text = "\n".join(block["text"] for block in client.payloads[1]["system"])
    second_message_text = client.payloads[1]["messages"][0]["content"][0]["text"]

    assert first_state["current_goal"] == "Continue the same improvement"
    assert "[FOCUS]" in second_system_text
    assert "Add compact session memory" in second_system_text
    assert "[FOCUS]" not in second_message_text
