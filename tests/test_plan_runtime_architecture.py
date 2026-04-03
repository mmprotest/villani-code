from __future__ import annotations

from pathlib import Path

import pytest

from villani_code.permissions import Decision
from villani_code.plan_session import PlanSessionResult
from villani_code.state import Runner, format_plan_text_to_artifact
from villani_code.state_tooling import execute_tool_with_policy


class SequencedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.last_payload = None

    def create_message(self, payload, stream=False):
        _ = stream
        self.last_payload = payload
        if not self.responses:
            return {"content": [{"type": "text", "text": "done"}]}
        return self.responses.pop(0)


def _minimal_runner(repo: Path):
    runner = type("R", (), {})()
    runner.repo = repo
    runner.model = "demo"
    runner.permissions = type("Perm", (), {"evaluate_with_reason": lambda self, *_a, **_k: type("P", (), {"decision": Decision.ALLOW, "reason": ""})()})()
    runner.hooks = type("Hooks", (), {"run_event": lambda self, *_a, **_k: type("R", (), {"allow": True, "reason": ""})()})()
    runner.small_model = False
    runner.villani_mode = False
    runner.benchmark_config = type("B", (), {"enabled": False})()
    runner._planning_read_only = True
    runner.bypass_permissions = True
    runner.auto_accept_edits = True
    runner.unsafe = False
    runner.checkpoints = type("C", (), {"create": lambda self, *_a, **_k: None})()
    runner._intended_targets = set()
    runner._current_verification_targets = set()
    runner._current_verification_before_contents = {}
    runner._before_contents = {}
    runner.event_callback = lambda _e: None
    runner._small_model_tool_guard = lambda *_a, **_k: None
    runner._tighten_tool_input = lambda *_a, **_k: None
    runner._emit_policy_event = lambda *_a, **_k: None
    return runner


def test_plan_finalizes_via_submit_plan_artifact_and_reads_multiple_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "villani_code").mkdir()
    (tmp_path / "villani_code" / "state.py").write_text("def plan():\n    pass\n", encoding="utf-8")
    (tmp_path / "villani_code" / "state_runtime.py").write_text("def run():\n    pass\n", encoding="utf-8")

    responses = [
        {"content": [{"type": "tool_use", "id": "1", "name": "Read", "input": {"file_path": "villani_code/state.py"}}]},
        {"content": [{"type": "tool_use", "id": "2", "name": "Read", "input": {"file_path": "villani_code/state_runtime.py"}}]},
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "3",
                    "name": "SubmitPlan",
                    "input": {
                        "task_summary": "Fix runtime plan finalization",
                        "candidate_files": ["villani_code/state.py", "villani_code/state_runtime.py"],
                        "assumptions": ["Planning mode is read-only"],
                        "recommended_steps": [
                            "Read villani_code/state.py to confirm /plan currently parses text output",
                            "Update villani_code/state_runtime.py to treat SubmitPlan as explicit finalization",
                            "Add tests in tests/test_plan_runtime_architecture.py for finalization and quality gating",
                        ],
                        "open_questions": [],
                        "risk_level": "medium",
                        "confidence_score": 0.9,
                    },
                }
            ]
        },
    ]
    runner = Runner(SequencedClient(responses), tmp_path, model="demo", stream=False)
    monkeypatch.setattr("villani_code.state.generate_execution_plan", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("fallback should not be used")))

    result = runner.plan("Find the biggest bug in this repo and make a plan to fix it")
    assert result.ready_to_execute is True
    assert "villani_code/state.py" in result.candidate_files
    assert "villani_code/state_runtime.py" in result.candidate_files


def test_planning_rejects_generic_artifact(tmp_path: Path, monkeypatch) -> None:
    runner = Runner(SequencedClient([]), tmp_path, model="demo", stream=False)

    def fake_run(*_a, **_k):
        runner._finalized_plan_artifact = {
            "task_summary": "generic",
            "candidate_files": ["villani_code/state.py", "villani_code/state_runtime.py"],
            "assumptions": ["a"],
            "recommended_steps": ["Inspect architecture", "Prioritize findings", "Prepare execution order"],
            "open_questions": [],
        }
        return {"response": {"content": [{"type": "text", "text": "draft"}]}}

    monkeypatch.setattr(runner, "run", fake_run)
    result = runner.plan("Find the biggest bug in this repo and make a plan to fix it")
    assert result.confidence_score == 0.35
    assert result.ready_to_execute is False


def test_execute_consumes_finalized_plan(monkeypatch, tmp_path: Path) -> None:
    runner = Runner(SequencedClient([]), tmp_path, model="demo", stream=False)
    captured = {}

    def fake_run(instruction: str, **_kwargs):
        captured["instruction"] = instruction
        captured["force_task_mode"] = _kwargs.get("force_task_mode")
        captured["approved_plan"] = _kwargs.get("approved_plan")
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}

    monkeypatch.setattr(runner, "run", fake_run)
    plan = PlanSessionResult(
        instruction="orig",
        task_summary="summary",
        recommended_steps=["Step 1"],
        assumptions=["A"],
        ready_to_execute=True,
    )
    runner.run_with_plan(plan)
    assert captured["instruction"] == "orig"
    assert captured["approved_plan"] is plan
    assert captured["force_task_mode"].value == "general"


def test_run_with_plan_forces_execution_mode_even_if_instruction_has_plan_words(monkeypatch, tmp_path: Path) -> None:
    runner = Runner(SequencedClient([]), tmp_path, model="demo", stream=False)
    seen: dict[str, object] = {}

    def fake_run(instruction: str, **kwargs):
        seen["instruction"] = instruction
        seen["force_task_mode"] = kwargs.get("force_task_mode")
        seen["approved_plan"] = kwargs.get("approved_plan")
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}

    monkeypatch.setattr(runner, "run", fake_run)
    plan = PlanSessionResult(
        instruction="inspect current behavior and then execute fix",
        task_summary="Implement controller fix",
        recommended_steps=["Edit villani_code/state.py"],
        assumptions=["carry forward approved constraints"],
        ready_to_execute=True,
    )
    runner.run_with_plan(plan)
    assert str(seen["instruction"]) == "inspect current behavior and then execute fix"
    assert seen["approved_plan"] is plan
    assert seen["force_task_mode"] is not None


def test_run_injects_approved_plan_context_into_normal_message_flow(tmp_path: Path) -> None:
    client = SequencedClient([{"content": [{"type": "text", "text": "done"}]}])
    runner = Runner(client, tmp_path, model="demo", stream=False)
    plan = PlanSessionResult(
        instruction="inspect current behavior and plan the fix",
        task_summary="Implement fix using approved plan",
        candidate_files=["villani_code/tui/controller.py"],
        recommended_steps=["Edit controller execute path", "Run tests"],
        assumptions=["Run validation before completion"],
        ready_to_execute=True,
    )

    runner.run(plan.instruction, approved_plan=plan)

    messages = client.last_payload["messages"]
    user_content = messages[0]["content"]
    merged = "\n".join(str(block.get("text", "")) for block in user_content if isinstance(block, dict))
    assert "<approved-plan-context>" in merged
    assert "Approved objective: Implement fix using approved plan" in merged
    assert runner._task_mode.value == "general"


def test_planning_recovers_strict_json_text_without_submit_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(SequencedClient([]), tmp_path, model="demo", stream=False)

    payload = {
        "task_summary": "Build pygame starter game",
        "candidate_files": ["game.py"],
        "assumptions": ["pygame available"],
        "recommended_steps": [
            "Create game.py with pygame init and main loop",
            "Implement player movement and simple obstacle collision",
            "Run python game.py and validate input/movement behavior",
        ],
        "open_questions": [],
    }

    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": __import__("json").dumps(payload)}]}},
    )
    result = runner.plan("Build me a pygame game from scratch")
    assert result.task_summary == "Build pygame starter game"
    assert result.ready_to_execute is True
    assert result.confidence_score != 0.35


def test_planning_recovers_plain_text_plan_without_submit_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(SequencedClient([]), tmp_path, model="demo", stream=False)
    text = "\n".join(
        [
            "Objective: Fix planner readiness behavior",
            "Files:",
            "- villani_code/state.py",
            "- tests/test_plan_runtime_architecture.py",
            "Steps:",
            "1. Update villani_code/state.py to parse assistant text into artifact fields.",
            "2. Keep SubmitPlan optional in villani_code/state.py and preserve strict JSON recovery.",
            "3. Run pytest tests/test_plan_runtime_architecture.py to validate behavior.",
            "Validation:",
            "- pytest tests/test_plan_runtime_architecture.py",
        ]
    )
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": text}]}})
    result = runner.plan("Fix a defect in this repo")
    assert result.task_summary == "Fix planner readiness behavior"
    assert result.ready_to_execute is True
    assert "villani_code/state.py" in result.candidate_files


def test_planning_falls_back_for_unusable_plain_text_and_is_not_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(SequencedClient([]), tmp_path, model="demo", stream=False)
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": "I will inspect files and provide a plan soon."}]}}
    )
    result = runner.plan("Fix a defect in this repo")
    assert result.confidence_score == 0.35
    assert result.ready_to_execute is False


def test_format_plan_text_to_artifact_recovers_core_fields() -> None:
    text = "\n".join(
        [
            "Objective: Stabilize planning conversion",
            "Files:",
            "- villani_code/state.py",
            "- tests/test_plan_workflow.py",
            "Steps:",
            "- Update plan parser in runtime.",
            "- Add tests for plain-text plan recovery.",
            "Validation:",
            "- pytest tests/test_plan_workflow.py",
            "Open Questions:",
            "- Should recovery tolerate missing headings?",
        ]
    )
    artifact = format_plan_text_to_artifact("Fallback objective", text)
    assert artifact["task_summary"] == "Stabilize planning conversion"
    assert "villani_code/state.py" in artifact["candidate_files"]
    assert artifact["recommended_steps"]
    assert any("pytest" in item for item in artifact["assumptions"])
    assert artifact["open_questions"]


def test_planning_mode_blocks_write_patch_and_mutating_bash(tmp_path: Path) -> None:
    runner = _minimal_runner(tmp_path)
    assert execute_tool_with_policy(runner, "Write", {"file_path": "a.txt", "content": "x"}, "1", 0)["is_error"]
    assert execute_tool_with_policy(runner, "Patch", {"unified_diff": "--- a/a\n+++ b/a\n"}, "2", 0)["is_error"]
    assert execute_tool_with_policy(runner, "Bash", {"command": "git commit -m x"}, "3", 0)["is_error"]
