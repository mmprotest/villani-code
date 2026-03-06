from __future__ import annotations

from pathlib import Path

import pytest

from villani_code.state import Runner
from villani_code import state_runtime


class DummyClient:
    def create_message(self, _payload, stream):
        return {"content": [{"type": "text", "text": "ok"}]}


def _seed_repo(repo: Path) -> None:
    (repo / "villani_code").mkdir(parents=True, exist_ok=True)
    (repo / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


def test_villani_high_risk_plan_auto_approved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=True)
    events: list[dict] = []
    runner.event_callback = events.append

    def deny_if_called(_name: str, _payload: dict) -> bool:
        raise AssertionError("approval callback must not be called in villani mode")

    runner.approval_callback = deny_if_called

    state_runtime.ensure_project_memory_and_plan(runner, "delete files and rewrite history")

    event_types = [e.get("type") for e in events]
    assert "plan_auto_approved" in event_types
    assert "plan_aborted" not in event_types
    assert "plan_approval_required" not in event_types


def test_non_villani_high_risk_plan_rejection_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=False)
    asked = {"count": 0}

    def reject(_name: str, _payload: dict) -> bool:
        asked["count"] += 1
        return False

    runner.approval_callback = reject
    with pytest.raises(RuntimeError, match="Execution plan rejected"):
        state_runtime.ensure_project_memory_and_plan(runner, "delete files and rewrite history")
    assert asked["count"] == 1


class _RetrieverHit:
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason


class _RetrieverStub:
    def query(self, _text: str, k: int = 8):
        return [_RetrieverHit("villani_code/state_runtime.py", "runtime behavior")]


class _RunnerStub:
    def __init__(self) -> None:
        self._retriever = _RetrieverStub()


def test_inject_retrieval_briefing_skips_tool_result_user_turn() -> None:
    runner = _RunnerStub()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_123", "content": "ok"},
                {"type": "text", "text": "Summarize result."},
            ],
        }
    ]
    original_message_content = [dict(block) for block in messages[0]["content"]]

    state_runtime.inject_retrieval_briefing(runner, messages)

    assert messages[0]["content"] == original_message_content
    assert all(
        not (isinstance(block, dict) and "<retrieval-briefing>" in str(block.get("text", "")))
        for block in messages[0]["content"]
    )


def test_inject_retrieval_briefing_inserts_for_plain_text_user_turn() -> None:
    runner = _RunnerStub()
    messages = [{"role": "user", "content": [{"type": "text", "text": "Need context on runtime."}]}]

    state_runtime.inject_retrieval_briefing(runner, messages)

    assert messages[0]["content"][0]["type"] == "text"
    assert "<retrieval-briefing>" in messages[0]["content"][0]["text"]
    assert messages[0]["content"][1] == {"type": "text", "text": "Need context on runtime."}
