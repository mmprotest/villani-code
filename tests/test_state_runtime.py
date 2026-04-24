from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from villani_code.state import Runner
from villani_code import state_runtime


class DummyClient:
    def create_message(self, _payload, stream):
        return {"content": [{"type": "text", "text": "ok"}]}


class _Inventory:
    def __init__(self) -> None:
        self.task_id = ""


class _GovernanceStub:
    def load_inventory(self):
        return _Inventory()

    def register_item(self, *args, **kwargs):
        return None

    def prune_for_budget(self, *args, **kwargs):
        return None

    def save_inventory(self, *args, **kwargs):
        return None


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


def test_prepare_messages_for_regular_runner_injects_execution_memento(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=False)
    runner._context_governance = _GovernanceStub()
    runner._ensure_mission("fix parser")
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "sys"}]},
        {"role": "user", "content": [{"type": "text", "text": "do it"}]},
    ]
    prepared = state_runtime.prepare_messages_for_model(runner, messages)
    flattened = str(prepared)
    assert "EXECUTION STATE" in flattened
    assert "Mission context packet:" not in flattened


def test_prepare_messages_for_model_does_not_duplicate_memento_injection(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=False)
    runner._context_governance = _GovernanceStub()
    runner._ensure_mission("fix parser")
    messages = [{"role": "user", "content": [{"type": "text", "text": "do it"}]}]
    first = state_runtime.prepare_messages_for_model(runner, messages)
    second = state_runtime.prepare_messages_for_model(runner, first)
    count = str(second).count("EXECUTION STATE")
    assert count == 1


def test_prepare_messages_for_model_regular_trim_keeps_tool_sequence_integrity(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=False)
    runner._context_governance = _GovernanceStub()
    runner._ensure_mission("fix parser")
    messages = [{"role": "system", "content": [{"type": "text", "text": "sys"}]}]
    for idx in range(8):
        messages.extend(
            [
                {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{idx}", "name": "Read", "input": {"file_path": "a.py"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{idx}", "content": "ok", "is_error": False}]},
            ]
        )
    messages.append({"role": "user", "content": [{"type": "text", "text": "latest task"}]})
    prepared = state_runtime.prepare_messages_for_model(runner, messages)
    assert len(prepared) < len(messages)
    state_runtime.validate_anthropic_tool_sequence(prepared)


def test_prepare_messages_falls_back_when_memento_required_fields_missing(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=False)
    runner._context_governance = _GovernanceStub()
    runner._ensure_mission("fix parser")
    if runner._mission_state is not None:
        memento_path = tmp_path / ".villani_code" / "missions" / runner._mission_state.mission_id / "execution_memento.json"
        payload = json.loads(memento_path.read_text(encoding="utf-8"))
        payload["objective"] = ""
        memento_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    messages = [{"role": "user", "content": [{"type": "text", "text": "do it"}]}]
    prepared = state_runtime.prepare_messages_for_model(runner, messages)
    assert "EXECUTION STATE" not in str(prepared)


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
    assert "last_validation_target: [\"tests/test_x.py\"]" in out
    assert "validation_repeated_without_new_evidence: false" in out
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
    assert "<validation_summary>" in out
    assert "last_validation_target: [\"src/a.py\"]" in out
    assert any(cmd[:2] == ["bash", "-lc"] for cmd in seen)


def test_repeated_stale_verification_returns_compact_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._verification_baseline_changed = set()
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: [])

    first = runner._run_verification("edit")
    second = runner._run_verification("edit")
    third = runner._run_verification("edit")
    fourth = runner._run_verification("edit")
    assert first.startswith("<validation_summary>")
    assert second.startswith("<validation_summary>")
    assert third.startswith("<validation_summary>")
    assert fourth == ""


def test_verification_reports_locked_scope_without_attributable_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._verification_baseline_changed = set()
    runner._intended_targets = {"villani_code/__init__.py"}
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: [])

    out = runner._run_verification("edit")
    assert "last_validation_target: []" in out
    assert "last_validation_summary:" in out


def test_verification_detail_event_keeps_raw_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    events: list[dict[str, object]] = []
    runner.event_callback = events.append
    runner._verification_baseline_changed = set()
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: [])

    out = runner._run_verification("edit")

    detail = next(e for e in events if e.get("type") == "verification_detail")
    assert "<verification>" in str(detail.get("detail", ""))
    assert "last_validation_summary:" in out


def test_repeated_validation_updates_compact_state_without_repeated_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._verification_baseline_changed = set()
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: ["villani_code/__init__.py"])

    first = runner._run_verification("edit")
    second = runner._run_verification("edit")
    third = runner._run_verification("edit")
    fourth = runner._run_verification("edit")

    assert "validation_repeated_without_new_evidence: false" in first
    assert "validation_repeated_without_new_evidence: true" in second
    assert "status repeated without new validation evidence" in third
    assert fourth == ""
    assert runner._validation_repeated_without_new_evidence is True


def test_changed_validation_state_emits_new_compact_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner._verification_baseline_changed = set()
    changed_files = [["tests/test_x.py"], ["src/a.py"]]

    def fake_changed(_repo: Path) -> list[str]:
        return changed_files.pop(0)

    monkeypatch.setattr(state_runtime, "git_changed_files", fake_changed)

    def fake_run(cmd, **kwargs):
        class P:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return P()

    monkeypatch.setattr(state_runtime.subprocess, "run", fake_run)

    first = runner._run_verification("edit")
    second = runner._run_verification("edit")

    assert first.startswith("<validation_summary>")
    assert second.startswith("<validation_summary>")
    assert first != second


def test_repeated_validation_keeps_raw_detail_events_when_live_summary_deduped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)
    events: list[dict[str, object]] = []
    runner.event_callback = events.append
    runner._verification_baseline_changed = set()
    monkeypatch.setattr(state_runtime, "git_changed_files", lambda _repo: [])

    first = runner._run_verification("edit")
    second = runner._run_verification("edit")
    third = runner._run_verification("edit")
    fourth = runner._run_verification("edit")

    details = [event for event in events if event.get("type") == "verification_detail"]
    assert first.startswith("<validation_summary>")
    assert second.startswith("<validation_summary>")
    assert third.startswith("<validation_summary>")
    assert fourth == ""
    assert len(details) == 4
    assert all("<verification>" in str(event.get("detail", "")) for event in details)


def test_validation_dedup_uses_structured_fingerprint_not_only_summary_text(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, small_model=True)

    first = state_runtime._build_compact_validation_summary(
        runner,
        target='["src/a.py"]',
        summary="status=pass",
        repeated_without_new_evidence=False,
        artifact_signature='["bash -lc import-check"]',
    )
    second = state_runtime._build_compact_validation_summary(
        runner,
        target='["src/a.py"]',
        summary="status=pass",
        repeated_without_new_evidence=False,
        artifact_signature='["pytest -q tests/test_x.py"]',
    )

    assert first.startswith("<validation_summary>")
    assert second.startswith("<validation_summary>")


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


def test_inject_diagnosis_hint_targets_latest_safe_user_message() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "older"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]},
        {"role": "user", "content": [{"type": "text", "text": "latest prompt"}]},
    ]
    state_runtime.inject_diagnosis_hint(
        messages,
        {"target_file": "src/app/core.py", "bug_class": "logic", "fix_intent": "fix"},
    )
    assert messages[0]["content"][0]["text"] == "older"
    assert messages[2]["content"] == [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]
    assert "Likely diagnosis:" in messages[3]["content"][0]["text"]


def test_inject_diagnosis_hint_handles_string_user_content() -> None:
    messages = [{"role": "user", "content": "fix it"}]
    state_runtime.inject_diagnosis_hint(
        messages,
        {"target_file": "src/a.py", "bug_class": "logic", "fix_intent": "fix"},
    )
    assert isinstance(messages[0]["content"], str)
    assert "Likely diagnosis:" in messages[0]["content"]
    assert messages[0]["content"].endswith("fix it")


def test_inject_diagnosis_hint_skips_when_only_tool_result_turns_exist() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]},
    ]
    original = [dict(msg) for msg in messages]
    state_runtime.inject_diagnosis_hint(
        messages,
        {"target_file": "src/a.py", "bug_class": "logic", "fix_intent": "fix"},
    )
    assert messages == original


def test_prepare_messages_for_model_deepcopies_before_injection() -> None:
    class _CtxGov:
        def load_inventory(self):
            return SimpleNamespace(task_id="")

        def register_item(self, *args, **kwargs):
            return None

        def prune_for_budget(self, _inventory):
            return None

        def save_inventory(self, _inventory):
            return None

    runner = SimpleNamespace(
        small_model=True,
        _retriever=_RetrieverStub(),
        _context_budget=None,
        _context_governance=_CtxGov(),
        _execution_plan=SimpleNamespace(task_goal="t"),
    )
    messages = [{"role": "user", "content": [{"type": "text", "text": "Need context on runtime."}]}]
    original = [{"role": "user", "content": [{"type": "text", "text": "Need context on runtime."}]}]

    prepared = state_runtime.prepare_messages_for_model(runner, messages)

    assert messages == original
    assert prepared[0]["content"][0]["type"] == "text"
    assert "<retrieval-briefing>" in prepared[0]["content"][0]["text"]


def test_prepare_messages_for_model_repeated_calls_do_not_duplicate_injection() -> None:
    class _CtxGov:
        def load_inventory(self):
            return SimpleNamespace(task_id="")

        def register_item(self, *args, **kwargs):
            return None

        def prune_for_budget(self, _inventory):
            return None

        def save_inventory(self, _inventory):
            return None

    runner = SimpleNamespace(
        small_model=True,
        _retriever=_RetrieverStub(),
        _context_budget=None,
        _context_governance=_CtxGov(),
        _execution_plan=SimpleNamespace(task_goal="t"),
    )
    messages = [{"role": "user", "content": [{"type": "text", "text": "Need context on runtime."}]}]
    prepared_one = state_runtime.prepare_messages_for_model(runner, messages)
    prepared_two = state_runtime.prepare_messages_for_model(runner, messages)
    for prepared in (prepared_one, prepared_two):
        assert sum(
            1
            for block in prepared[0]["content"]
            if isinstance(block, dict) and "<retrieval-briefing>" in str(block.get("text", ""))
        ) == 1


def test_fail_first_localization_runs_without_strong_signal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    events: list[dict] = []
    runner = SimpleNamespace(
        repo=tmp_path,
        benchmark_config=SimpleNamespace(visible_verification=["pytest -q"], expected_files=[]),
        _execution_plan=SimpleNamespace(relevant_files=[]),
        _pending_verification="",
        event_callback=events.append,
    )

    def fake_run(cmd, **_kwargs):
        assert cmd == ["bash", "-lc", "pytest -q"]

        class P:
            returncode = 1
            stdout = "FAILED tests/test_api.py::test_runtime - AssertionError: boom"
            stderr = ""

        return P()

    monkeypatch.setattr(state_runtime.subprocess, "run", fake_run)
    evidence = state_runtime.run_pre_edit_failure_localization(runner)

    assert evidence is not None
    assert evidence["first_failing_test"] == "tests/test_api.py::test_runtime"
    assert any(e.get("type") == "pre_edit_failure_signal_attempted" for e in events)
    assert any(e.get("type") == "pre_edit_failure_signal_captured" for e in events)


def test_fail_first_localization_skips_with_clear_expected_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    events: list[dict] = []
    runner = SimpleNamespace(
        repo=tmp_path,
        benchmark_config=SimpleNamespace(
            visible_verification=["pytest -q"],
            expected_files=["src/app/core.py"],
        ),
        _execution_plan=SimpleNamespace(relevant_files=[]),
        _pending_verification="",
        event_callback=events.append,
    )

    def fake_run(_cmd, **_kwargs):
        raise AssertionError("verification should be skipped when expected file is clear")

    monkeypatch.setattr(state_runtime.subprocess, "run", fake_run)
    evidence = state_runtime.run_pre_edit_failure_localization(runner)

    assert evidence is None
    assert any(e.get("type") == "pre_edit_failure_signal_skipped" for e in events)


def test_parse_failure_signal_extracts_test_and_traceback() -> None:
    output = (
        "FAILED tests/test_core.py::test_final_page - AssertionError: expected 5\n"
        'File "src/app/core.py", line 27, in paginate\n'
        "E   AssertionError: expected 5"
    )
    evidence = state_runtime.parse_failure_signal(output, "")
    assert evidence["first_failing_test"] == "tests/test_core.py::test_final_page"
    assert evidence["traceback_file"] == "src/app/core.py"
    assert evidence["traceback_line"] == 27
    assert "expected 5" in evidence["error_summary"]


def test_run_pre_edit_diagnosis_injects_failure_evidence_context() -> None:
    captured: dict[str, object] = {}

    class Client:
        def create_message(self, payload, stream):
            captured["payload"] = payload
            return {
                "content": [
                    {
                        "type": "text",
                        "text": '{"target_file":"src/app/core.py","bug_class":"logic_error","fix_intent":"Fix pagination terminal condition."}',
                    }
                ]
            }

    events: list[dict] = []
    runner = SimpleNamespace(
        model="m",
        max_tokens=300,
        client=Client(),
        event_callback=events.append,
        benchmark_config=SimpleNamespace(enabled=True, visible_verification=["pytest -q"], expected_files=[]),
        _execution_plan=SimpleNamespace(validation_steps=["pytest -q"], relevant_files=[]),
    )
    diagnosis = state_runtime.run_pre_edit_diagnosis(
        runner,
        "fix bug",
        failure_evidence={
            "first_failing_test": "tests/test_core.py::test_final_page",
            "traceback_file": "src/app/core.py",
            "traceback_line": 27,
            "error_summary": "AssertionError: expected 5",
            "raw_failure_excerpt": "FAILED tests/test_core.py::test_final_page",
        },
    )
    prompt_text = captured["payload"]["messages"][0]["content"][0]["text"]
    assert "First failing test: tests/test_core.py::test_final_page" in prompt_text
    assert "Traceback location: src/app/core.py:27" in prompt_text
    assert "Error summary: AssertionError: expected 5" in prompt_text
    assert diagnosis is not None


def test_fail_first_localization_handles_unparseable_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path)
    events: list[dict] = []
    runner = SimpleNamespace(
        repo=tmp_path,
        benchmark_config=SimpleNamespace(visible_verification=["pytest -q"], expected_files=[]),
        _execution_plan=SimpleNamespace(relevant_files=[]),
        _pending_verification="",
        event_callback=events.append,
    )

    def fake_run(_cmd, **_kwargs):
        class P:
            returncode = 1
            stdout = ""
            stderr = ""

        return P()

    monkeypatch.setattr(state_runtime.subprocess, "run", fake_run)
    evidence = state_runtime.run_pre_edit_failure_localization(runner)

    assert evidence is not None
    assert evidence["first_failing_test"] == ""
    assert evidence["traceback_file"] == ""
    assert any(e.get("type") == "pre_edit_failure_signal_captured" for e in events)


def test_fail_first_localization_uses_isolated_workspace(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    (tmp_path / "marker.txt").write_text("live\n", encoding="utf-8")
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "test_isolated.py").write_text(
        "from pathlib import Path\n\n"
        "def test_mutates_marker():\n"
        "    Path('marker.txt').write_text('isolated', encoding='utf-8')\n"
        "    assert False\n",
        encoding="utf-8",
    )
    events: list[dict] = []
    runner = SimpleNamespace(
        repo=tmp_path,
        benchmark_config=SimpleNamespace(visible_verification=["pytest -q"], expected_files=[]),
        _execution_plan=SimpleNamespace(relevant_files=[]),
        _pending_verification="",
        event_callback=events.append,
    )

    evidence = state_runtime.run_pre_edit_failure_localization(runner)

    assert evidence is not None
    assert evidence["exit_code"] != 0
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "live\n"
    assert any(e.get("type") == "pre_edit_failure_signal_isolated" for e in events)


def test_diagnosis_confidence_strong_with_traceback_match(tmp_path: Path) -> None:
    runner = SimpleNamespace(
        benchmark_config=SimpleNamespace(expected_files=[]),
        _execution_plan=SimpleNamespace(relevant_files=[]),
    )
    diagnosis = {
        "target_file": "src/app/core.py",
        "bug_class": "logic_error",
        "fix_intent": "Fix boundary condition.",
    }
    confidence = state_runtime.classify_diagnosis_target_confidence(
        runner,
        diagnosis,
        failure_evidence={"traceback_file": "src/app/core.py"},
    )
    assert confidence == "strong"


def test_diagnosis_confidence_weak_without_file_evidence(tmp_path: Path) -> None:
    runner = SimpleNamespace(
        benchmark_config=SimpleNamespace(expected_files=[]),
        _execution_plan=SimpleNamespace(relevant_files=[]),
    )
    diagnosis = {
        "target_file": "src/app/core.py",
        "bug_class": "logic_error",
        "fix_intent": "Try likely core file.",
    }
    confidence = state_runtime.classify_diagnosis_target_confidence(runner, diagnosis, failure_evidence=None)
    assert confidence == "weak"
