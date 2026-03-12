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


def test_validate_anthropic_tool_sequence_rejects_text_after_tool_result() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "Ls", "input": {"path": "."}}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok", "is_error": False},
                {"type": "text", "text": "extra"},
            ],
        },
    ]

    with pytest.raises(RuntimeError, match="message index 0"):
        state_runtime.validate_anthropic_tool_sequence(messages)


def test_validate_anthropic_tool_sequence_rejects_missing_followup_user_message() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "Ls", "input": {"path": "."}}],
        }
    ]

    with pytest.raises(RuntimeError, match="message index 0"):
        state_runtime.validate_anthropic_tool_sequence(messages)


def test_validate_anthropic_tool_sequence_rejects_non_user_followup() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "Ls", "input": {"path": "."}}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "not allowed"}]},
    ]

    with pytest.raises(RuntimeError, match="message index 0"):
        state_runtime.validate_anthropic_tool_sequence(messages)


def test_run_verification_targets_touched_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._verification_baseline_changed = set()
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: ["tests/test_x.py"])
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        class P:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return P()

    monkeypatch.setattr(state_runtime.subprocess, "run", fake_run)
    out = runner._run_verification("edit")
    assert "pytest -q tests/test_x.py" in out
    assert any(cmd[:2] == ["pytest", "-q"] for cmd in seen)


def test_run_verification_uses_baseline_import_for_touched_python_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("x=1\n", encoding="utf-8")
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._verification_baseline_changed = set()
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: ["src/a.py"])
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        class P:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return P()

    monkeypatch.setattr(state_runtime.subprocess, "run", fake_run)
    out = runner._run_verification("edit")
    assert "python -c" in out
    assert any(cmd[:2] == ["bash", "-lc"] for cmd in seen)


def test_repeated_stale_verification_returns_compact_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._verification_baseline_changed = set()
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: [])

    for _ in range(2):
        runner._run_verification("edit")
    third = runner._run_verification("edit")
    assert "verification state repeated" in third
    assert third.startswith("<verification>")


def test_verification_reports_locked_scope_without_attributable_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._verification_baseline_changed = set()
    runner._intended_targets = {"villani_code/__init__.py"}
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: [])

    out = runner._run_verification("edit")
    assert "no intentional diff is currently attributable in locked scope" in out


def test_parse_pre_edit_diagnosis_accepts_strict_json() -> None:
    raw = '{"target_file":"src/app/config.py","bug_class":"wrong_precedence","fix_intent":"Prefer env over file values."}'
    parsed = state_runtime.parse_pre_edit_diagnosis(raw)
    assert parsed == {
        "target_file": "src/app/config.py",
        "bug_class": "wrong_precedence",
        "fix_intent": "Prefer env over file values.",
    }


def test_parse_pre_edit_diagnosis_rejects_invalid_json() -> None:
    assert state_runtime.parse_pre_edit_diagnosis('not json') is None
    assert state_runtime.parse_pre_edit_diagnosis('{"target_file":"x"}') is None


def test_inject_diagnosis_hint_prepends_user_context() -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "fix bug"}]}]
    state_runtime.inject_diagnosis_hint(
        messages,
        {
            "target_file": "src/app/config.py",
            "bug_class": "wrong_precedence",
            "fix_intent": "Prefer env over file values.",
        },
    )
    first = messages[0]["content"][0]["text"]
    assert "Likely diagnosis:" in first
    assert "Target file: src/app/config.py" in first
    assert "Treat it as a hint, not ground truth." in first
