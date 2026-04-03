from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Input

from villani_code.permissions import Decision
from villani_code.plan_session import PlanAnswer, PlanOption, PlanQuestion, PlanSessionResult
from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy
from villani_code.tui.app import VillaniTUI
from villani_code.tui.controller import RunnerController
from villani_code.tui.widgets.plan_question import PlanQuestionWidget


class DummyClient:
    def create_message(self, payload, stream=False):
        _ = (payload, stream)
        return {
            "content": [
                {
                    "type": "text",
                    "text": __import__("json").dumps(
                        {
                            "task_summary": "Repo improvement plan",
                            "candidate_files": ["villani_code/state.py", "tests/test_plan_workflow.py"],
                            "assumptions": ["Planning mode remains read-only"],
                            "recommended_steps": [
                                "Inspect villani_code/state.py planning flow.",
                                "Update logic to preserve recovered artifacts.",
                                "Run pytest tests/test_plan_workflow.py.",
                            ],
                            "validation_approach": ["pytest tests/test_plan_workflow.py"],
                            "open_questions": [],
                        }
                    ),
                }
            ]
        }


class DummyRunnerForApp:
    model = "demo"
    permissions = None


class ControllerSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_prompt(self, text: str) -> None:
        self.calls.append(f"run:{text}")

    def run_plan_prompt(self, text: str) -> None:
        self.calls.append(f"plan:{text}")

    def run_execute_plan(self) -> None:
        self.calls.append("execute")

    def replan(self) -> None:
        self.calls.append("replan")

    def submit_plan_answer(self, answer: PlanAnswer) -> None:
        self.calls.append(answer.question_id)

    def resolve_approval(self, request_id: str, choice: str) -> None:
        return None


class MinimalRunner:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.model = "demo"
        self.permissions = type("Perm", (), {"evaluate_with_reason": lambda self, *_a, **_k: type("P", (), {"decision": Decision.ALLOW, "reason": ""})()})()
        self.hooks = type("Hooks", (), {"run_event": lambda self, *_a, **_k: type("R", (), {"allow": True, "reason": ""})()})()
        self.small_model = False
        self.villani_mode = False
        self.benchmark_config = type("B", (), {"enabled": False})()
        self._planning_read_only = True
        self.bypass_permissions = True
        self.auto_accept_edits = True
        self.unsafe = False
        self.checkpoints = type("C", (), {"create": lambda self, *_a, **_k: None})()
        self._intended_targets = set()
        self._current_verification_targets = set()
        self._current_verification_before_contents = {}
        self._before_contents = {}
        self.event_callback = lambda _e: None
        self._small_model_tool_guard = lambda *_a, **_k: None
        self._tighten_tool_input = lambda *_a, **_k: None
        self._emit_policy_event = lambda *_a, **_k: None


class PlanningRunnerStub:
    print_stream = False
    approval_callback = None
    event_callback = None
    permissions = None

    def __init__(self) -> None:
        self.plan_calls: list[tuple[str, list[PlanAnswer]]] = []

    def plan(self, instruction: str, answers: list[PlanAnswer] | None = None) -> PlanSessionResult:
        resolved = list(answers or [])
        self.plan_calls.append((instruction, resolved))
        open_questions = [] if resolved else [
            PlanQuestion(
                id="scope",
                question="Pick scope",
                rationale="Need one",
                options=[
                    PlanOption("s1", "Single-file", "single"),
                    PlanOption("s2", "Narrow", "narrow"),
                    PlanOption("s3", "Broad", "broad"),
                    PlanOption("so", "Other", "custom", is_other=True),
                ],
            )
        ]
        return PlanSessionResult(
            instruction=instruction,
            task_summary="Real task summary",
            candidate_files=["villani_code/tui/app.py"],
            assumptions=["assume a"],
            recommended_steps=["inspect file", "prepare plan"],
            open_questions=open_questions,
            resolved_answers=resolved,
            ready_to_execute=not open_questions,
            execution_brief="brief",
            risk_level="medium",
            confidence_score=0.7,
        )

    def run(self, instruction: str, messages=None, execution_budget=None, approved_plan=None):
        _ = (messages, execution_budget, approved_plan)
        return {"response": {"content": [{"type": "text", "text": instruction}]}}

    def run_with_plan(self, plan: PlanSessionResult):
        return {"response": {"content": [{"type": "text", "text": plan.task_summary}]}}

    def run_villani_mode(self):
        return {"response": {"content": []}}


class ThreadSafeApp:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.messages: list[object] = []
        self._instruction = "task"
        self._answers: list[PlanAnswer] = []
        self._stage = "idle"

    def post_message(self, message: object) -> object:
        self.messages.append(message)
        return message

    def call_from_thread(self, callback, *args, **kwargs):
        self.calls.append(getattr(callback, "__name__", str(callback)))
        return callback(*args, **kwargs)

    def apply_plan_result(self, _result: PlanSessionResult, _reset: bool) -> None:
        return None

    def record_plan_answer(self, answer: PlanAnswer) -> None:
        self._answers.append(answer)

    def get_plan_instruction(self) -> str:
        return self._instruction

    def get_plan_answers(self) -> list[PlanAnswer]:
        return list(self._answers)

    def get_last_ready_plan(self) -> PlanSessionResult | None:
        return None

    def set_plan_stage(self, stage: str) -> None:
        self._stage = stage


def test_plan_question_enforces_four_options_and_single_other() -> None:
    options = [
        PlanOption("a", "A", "one"),
        PlanOption("b", "B", "two"),
        PlanOption("c", "C", "three"),
        PlanOption("o", "Other", "custom", is_other=True),
    ]
    q = PlanQuestion(id="q1", question="Pick", rationale="Need choice", options=options)
    assert len(q.options) == 4
    assert sum(1 for o in q.options if o.is_other) == 1


def test_runner_plan_summary_does_not_include_planning_boilerplate(tmp_path: Path) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    instruction = "Look through the repo and find improvements I can make"
    result = runner.plan(instruction)
    assert result.task_summary == instruction
    assert "Create an implementation plan in read-only inspection mode" not in result.task_summary
    assert "Do not edit files" not in result.task_summary


def test_runner_plan_for_repo_review_has_concrete_steps_without_generic_questions(tmp_path: Path) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    result = runner.plan("Find improvements for this repo")
    assert result.ready_to_execute is True
    assert result.candidate_files
    assert result.open_questions == []
    generic_markers = (
        "survey high-signal areas",
        "map the smallest safe implementation scope",
        "identify concrete improvement candidates",
    )
    # If a generic marker appears, it must be accompanied by concrete file/module references.
    for step in result.recommended_steps:
        lowered = step.lower()
        if any(marker in lowered for marker in generic_markers):
            assert any(path in step for path in result.candidate_files)




def test_runner_plan_records_real_file_evidence(tmp_path: Path) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    result = runner.plan("Find improvements for this repo")
    assert any(item.startswith("Evidence inspected: ") for item in result.assumptions)



def test_plan_uses_runtime_loop_for_recovered_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    seen: dict[str, str] = {}

    def fake_run(instruction: str, messages=None, execution_budget=None):
        _ = (messages, execution_budget)
        seen["instruction"] = instruction
        payload = {
            "task_summary": "Runtime-derived summary",
            "candidate_files": ["villani_code/state.py"],
            "assumptions": ["a"],
            "recommended_steps": ["read file", "propose ordered edits"],
            "validation_approach": ["pytest tests/test_plan_workflow.py"],
            "open_questions": [],
            "risk_level": "medium",
            "confidence_score": 0.9,
        }
        return {"response": {"content": [{"type": "text", "text": __import__("json").dumps(payload)}]}}

    monkeypatch.setattr(runner, "run", fake_run)
    result = runner.plan("Find ways to improve this repo")
    assert "Create an implementation plan in read-only inspection mode" in seen["instruction"]
    assert result.task_summary == "Runtime-derived summary"
    assert result.ready_to_execute is True


def test_plan_runtime_prompt_contains_multi_file_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    monkeypatch.setattr(
        "villani_code.state._collect_planning_evidence",
        lambda *_a, **_k: [
            {"path": "villani_code/state.py", "excerpt": "def plan"},
            {"path": "villani_code/state_tooling.py", "excerpt": "def execute_tool_with_policy"},
        ],
    )

    def fake_run(instruction: str, messages=None, execution_budget=None):
        _ = (messages, execution_budget)
        assert "villani_code/state.py" in instruction
        assert "villani_code/state_tooling.py" in instruction
        payload = {
            "task_summary": "summary",
            "candidate_files": ["villani_code/state.py", "villani_code/state_tooling.py"],
            "assumptions": ["a"],
            "recommended_steps": ["read both files", "sequence changes"],
            "validation_approach": ["pytest"],
            "open_questions": [],
        }
        return {"response": {"content": [{"type": "text", "text": __import__("json").dumps(payload)}]}}

    monkeypatch.setattr(runner, "run", fake_run)
    result = runner.plan("Find ways to improve this repo")
    assert len(result.candidate_files) >= 2


def test_planning_read_only_blocks_mutating_git_bash(tmp_path: Path) -> None:
    runner = MinimalRunner(tmp_path)
    blocked = execute_tool_with_policy(runner, "Bash", {"command": "git commit -m 'x'"}, "1", 0)
    assert blocked["is_error"] is True
    assert "read-only" in blocked["content"].lower()


def test_planning_can_continue_after_clarification_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    calls = {"count": 0}

    def fake_run(instruction: str, messages=None, execution_budget=None):
        _ = (instruction, messages, execution_budget)
        calls["count"] += 1
        if calls["count"] == 1:
            payload = {
                "task_summary": "summary",
                "candidate_files": ["villani_code/state.py"],
                "assumptions": ["a"],
                "recommended_steps": ["step"],
                "validation_approach": ["pytest"],
                "open_questions": [
                    {
                        "id": "q1",
                        "question": "Pick mode",
                        "rationale": "Need decision",
                        "options": [
                            {"id": "a", "label": "A", "description": "A"},
                            {"id": "b", "label": "B", "description": "B"},
                            {"id": "c", "label": "C", "description": "C"},
                            {"id": "o", "label": "Other", "description": "O", "is_other": True},
                        ],
                    }
                ],
            }
        else:
            payload = {
                "task_summary": "summary",
                "candidate_files": ["villani_code/state.py"],
                "assumptions": ["a", "q1: a"],
                "recommended_steps": ["step", "step2"],
                "validation_approach": ["pytest"],
                "open_questions": [],
            }
        return {"response": {"content": [{"type": "text", "text": __import__("json").dumps(payload)}]}}

    monkeypatch.setattr(runner, "run", fake_run)
    first = runner.plan("Find ways to improve this repo")
    assert first.ready_to_execute is False
    second = runner.plan("Find ways to improve this repo", answers=[PlanAnswer("q1", "a")])
    assert second.ready_to_execute is True
    assert calls["count"] == 2

def test_plan_inline_prompt_starts_planning_immediately(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    app.controller = ControllerSpy()
    app.on_input_submitted(Input.Submitted(Input(id="input"), "/plan Find improvements in command flow"))
    assert app.controller.calls == ["plan:Find improvements in command flow"]


def test_bare_plan_enters_prompt_awaiting_mode(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    app.controller = ControllerSpy()
    app.on_input_submitted(Input.Submitted(Input(id="input"), "/plan"))
    assert app.planning_session.stage == "awaiting_prompt"
    assert app.controller.calls == []




def test_ready_plan_does_not_hijack_future_normal_prompts(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    app.controller = ControllerSpy()
    app.planning_session.latest_result = PlanSessionResult(instruction="task", task_summary="task", ready_to_execute=True)
    app.planning_session.last_ready_plan = app.planning_session.latest_result
    app.planning_session.stage = "ready"
    app.on_input_submitted(Input.Submitted(Input(id="input"), "normal prompt"))
    assert app.controller.calls == ["run:normal prompt"]


def test_clarification_only_for_real_design_uncertainty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: {
            "response": {
                "content": [
                    {
                        "type": "text",
                        "text": __import__("json").dumps(
                            {
                                "task_summary": "task",
                                "candidate_files": ["a.py"],
                                "assumptions": ["a"],
                                "recommended_steps": ["read a.py"],
                                "validation_approach": ["pytest"],
                                "open_questions": [
                                    {
                                        "id": "implementation_path",
                                        "question": "Pick one",
                                        "rationale": "Need decision",
                                        "options": [
                                            {"id": "a", "label": "A", "description": "a"},
                                            {"id": "b", "label": "B", "description": "b"},
                                            {"id": "c", "label": "C", "description": "c"},
                                            {"id": "o", "label": "Other", "description": "o", "is_other": True},
                                        ],
                                    }
                                ],
                            }
                        ),
                    }
                ]
            }
        },
    )
    result = runner.plan("Implement feature with ambiguous architecture")
    assert len(result.open_questions) == 1
    assert result.open_questions[0].id == "implementation_path"


def test_run_with_plan_uses_approved_plan_payload(tmp_path: Path) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    seen: dict[str, object] = {}

    def fake_run(instruction: str, messages=None, execution_budget=None, approved_plan=None, force_task_mode=None):
        seen["instruction"] = instruction
        seen["approved_plan"] = approved_plan
        seen["force_task_mode"] = force_task_mode
        return {"response": {"content": []}}

    runner.run = fake_run  # type: ignore[assignment]
    plan = PlanSessionResult(
        instruction="orig",
        task_summary="summary",
        assumptions=["assume"],
        recommended_steps=["step1"],
        resolved_answers=[PlanAnswer("q", "opt", "custom")],
        ready_to_execute=True,
        execution_brief="brief",
    )
    runner.run_with_plan(plan)
    assert seen["instruction"] == "orig"
    assert seen["approved_plan"] is plan
    assert seen["force_task_mode"] is not None


def test_run_with_plan_fails_if_unresolved(tmp_path: Path) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    plan = PlanSessionResult(instruction="orig", task_summary="summary", ready_to_execute=False)
    with pytest.raises(RuntimeError):
        runner.run_with_plan(plan)


def test_planning_read_only_blocks_write_tool(tmp_path: Path) -> None:
    runner = MinimalRunner(tmp_path)
    blocked = execute_tool_with_policy(runner, "Write", {"file_path": "a.txt", "content": "x"}, "1", 0)
    assert blocked["is_error"] is True
    assert "read-only" in blocked["content"].lower()


def test_controller_uses_call_from_thread_for_plan_ui_mutation() -> None:
    app = ThreadSafeApp()
    controller = RunnerController(PlanningRunnerStub(), app)
    controller._run_plan_prompt_worker("task")
    assert "apply_plan_result" in app.calls




def test_execute_runs_last_ready_plan(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    app.controller = ControllerSpy()
    plan = PlanSessionResult(instruction="a", task_summary="b", ready_to_execute=True)
    app.planning_session.latest_result = plan
    app.planning_session.last_ready_plan = plan
    app.planning_session.stage = "ready"
    app._execute_command_item(type("I", (), {"trigger": "/execute"})())
    assert app.controller.calls == ["execute"]


def test_execute_fails_cleanly_when_plan_unresolved(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    app.controller = ControllerSpy()
    app.planning_session.latest_result = PlanSessionResult(instruction="a", task_summary="b", ready_to_execute=False)
    app.planning_session.stage = "awaiting_clarification"
    app._execute_command_item(type("I", (), {"trigger": "/execute"})())
    assert "Cannot execute: no ready plan." in app._log_plain_text


def test_question_widget_visible_and_options_render_and_other_validation(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(PlanningRunnerStub(), tmp_path)
        async with app.run_test() as pilot:
            app.apply_plan_result(app.runner.plan("task"), reset_answers=True)
            await pilot.pause()
            widget = app.query_one(PlanQuestionWidget)
            assert widget.display
            assert widget.option_labels()[:3] == ["Single-file", "Narrow", "Broad"]
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            assert "Other requires non-empty text." in app._log_plain_text

    asyncio.run(run())


def test_other_option_allows_typing_custom_answer(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(PlanningRunnerStub(), tmp_path)
        async with app.run_test() as pilot:
            app.apply_plan_result(app.runner.plan("task"), reset_answers=True)
            await pilot.pause()
            await pilot.press("down", "down", "down")
            await pilot.pause()
            other_input = app.query_one("#plan-other-input", Input)
            assert other_input.display is True
            await pilot.press("c", "u", "s", "t", "o", "m")
            await pilot.pause()
            assert other_input.value == "custom"

    asyncio.run(run())


def test_other_option_enter_submits_clarification_without_main_prompt_submission(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(PlanningRunnerStub(), tmp_path)
        app.controller = ControllerSpy()
        async with app.run_test() as pilot:
            app.apply_plan_result(
                PlanSessionResult(
                    instruction="task",
                    task_summary="task",
                    open_questions=[
                        PlanQuestion(
                            id="q",
                            question="Which one?",
                            rationale="Need it",
                            options=[
                                PlanOption("a", "A", "a"),
                                PlanOption("b", "B", "b"),
                                PlanOption("c", "C", "c"),
                                PlanOption("o", "Other", "o", is_other=True),
                            ],
                        )
                    ],
                ),
                reset_answers=True,
            )
            await pilot.pause()
            await pilot.press("down", "down", "down")
            await pilot.pause()
            await pilot.press("x", "y", "z", "enter")
            await pilot.pause()
            assert app.controller.calls == ["q"]

    asyncio.run(run())


def test_final_answer_submission_triggers_replan(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(PlanningRunnerStub(), tmp_path)
        async with app.run_test() as pilot:
            app.apply_plan_result(app.runner.plan("task"), reset_answers=True)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.runner.plan_calls[-1][1]
            assert "Plan ready. Run /execute to implement." in app._log_plain_text

    asyncio.run(run())


def test_clarification_options_are_logged_to_transcript(tmp_path: Path) -> None:
    async def run() -> None:
        app = VillaniTUI(DummyRunnerForApp(), tmp_path)
        async with app.run_test() as pilot:
            app.apply_plan_result(
                PlanSessionResult(
                    instruction="task",
                    task_summary="task",
                    open_questions=[
                        PlanQuestion(
                            id="q",
                            question="Which one?",
                            rationale="Need it",
                            options=[
                                PlanOption("a", "A", "a"),
                                PlanOption("b", "B", "b"),
                                PlanOption("c", "C", "c"),
                                PlanOption("o", "Other", "o", is_other=True),
                            ],
                        )
                    ],
                ),
                reset_answers=True,
            )
            await pilot.pause()
            assert "Clarification 1/1: Which one?" in app._log_plain_text
            assert "[4] Other" in app._log_plain_text

    asyncio.run(run())


def test_main_input_submission_guard_ignores_plan_other_input(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    app.controller = ControllerSpy()
    app.on_input_submitted(Input.Submitted(Input(id="plan-other-input"), "custom"))
    assert app.controller.calls == []


def test_main_input_submission_still_routes_primary_input(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    app.controller = ControllerSpy()
    app.on_input_submitted(Input.Submitted(Input(id="input"), "hello"))
    assert app.controller.calls == ["run:hello"]


def test_plan_payload_dicts_are_normalized_for_clean_rendering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")

    fake_payload = {
        "task_summary": "Improve planning workflow",
        "candidate_files": [{"path": "villani_code/state.py", "improvement_focus": "planning control flow"}],
        "assumptions": [{"risk": "moderate", "mitigation": "targeted tests"}],
        "recommended_steps": [{"priority": "P1", "action": "Fix /plan routing"}],
        "validation_approach": [{"check": "pytest tests/test_plan_workflow.py"}],
        "open_questions": [],
        "risk_level": "medium",
        "confidence_score": 0.7,
    }

    monkeypatch.setattr("villani_code.state._collect_planning_evidence", lambda *_a, **_k: [{"path": "villani_code/state.py", "excerpt": "def plan"}])
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": __import__("json").dumps(fake_payload)}]}},
    )

    result = runner.plan("Find ways to improve this repo")
    assert result.ready_to_execute is True
    assert all("{" not in entry and "}" not in entry for entry in result.candidate_files)
    assert all("{" not in entry and "}" not in entry for entry in result.recommended_steps)
    assert any(item.startswith("Evidence inspected: ") for item in result.assumptions)


def test_repo_review_prompt_defaults_to_ready_plan_without_clarification(tmp_path: Path) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    result = runner.plan("Find ways to improve this repo")
    assert result.open_questions == []
    assert result.ready_to_execute is True


def test_runner_plan_inspects_real_repo_files_not_only_repo_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    touched: list[str] = []

    def fake_collect(repo: Path, instruction: str, repo_map: dict):
        _ = (instruction, repo_map)
        touched.append(str(repo / "villani_code/state.py"))
        return [{"path": "villani_code/state.py", "excerpt": "def plan"}]

    monkeypatch.setattr("villani_code.state._collect_planning_evidence", fake_collect)
    runner.plan("Find ways to improve this repo")
    assert touched


def test_apply_plan_result_renders_plain_english_without_raw_json(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    result = PlanSessionResult(
        instruction="task",
        task_summary="Fix planning output pipeline",
        candidate_files=["villani_code/state.py", "villani_code/tui/controller.py"],
        assumptions=["Validate with targeted pytest"],
        recommended_steps=["Inspect planning parser behavior", "Suppress planning stream text in TUI controller"],
        open_questions=[],
        ready_to_execute=True,
        risk_level="medium",
        confidence_score=0.82,
    )
    app.apply_plan_result(result, reset_answers=True)
    assert "Plan:" in app._log_plain_text
    assert "Implementation steps:" in app._log_plain_text
    assert "{" not in app._log_plain_text
    assert "}" not in app._log_plain_text


def test_apply_plan_result_for_non_ready_plan_does_not_log_execute_hint(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)
    result = PlanSessionResult(
        instruction="Fix planner flow",
        task_summary="Recovered plan needs details",
        candidate_files=["villani_code/state.py"],
        recommended_steps=["Re-run /replan after reviewing evidence"],
        assumptions=["Waiting for validation command details."],
        ready_to_execute=False,
    )
    app.apply_plan_result(result, reset_answers=True)
    assert "Plan ready. Run /execute to implement." not in app._log_plain_text
    assert app.planning_session.stage == "idle"


def test_plan_flow_from_mixed_narrative_json_renders_only_final_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    app = VillaniTUI(DummyRunnerForApp(), tmp_path)

    payload = {
        "task_summary": "Fix transcript leaks",
        "candidate_files": ["villani_code/tui/controller.py"],
        "assumptions": ["Planning should remain read-only"],
        "recommended_steps": ["Suppress planning stream text", "Add parser regression tests"],
        "open_questions": [],
    }
    mixed = "Narrative preface that should not render.\n```json\n" + __import__("json").dumps(payload) + "\n```\nTrailing note."
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": mixed}]}})

    result = runner.plan("Find the most important bug and plan fix")
    app.apply_plan_result(result, reset_answers=True)
    assert "Narrative preface" not in app._log_plain_text
    assert "```json" not in app._log_plain_text
    assert "Suppress planning stream text" in app._log_plain_text


def test_greenfield_plan_with_single_candidate_file_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    payload = {
        "task_summary": "Build a pygame game",
        "candidate_files": ["game.py"],
        "assumptions": ["Python and pygame are available"],
        "recommended_steps": [
            "Create game.py and initialize pygame display and clock.",
            "Implement player movement, scoring, and collision logic in game.py.",
            "Run python game.py and validate controls/render loop behavior.",
        ],
        "open_questions": [],
    }
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": __import__("json").dumps(payload)}]}},
    )
    result = runner.plan("Build me a pygame game from scratch")
    assert result.ready_to_execute is True
    assert result.candidate_files == ["game.py"]


def test_greenfield_plan_without_validation_is_not_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    payload = {
        "task_summary": "Build a pygame game",
        "candidate_files": [],
        "assumptions": ["Python and pygame are available"],
        "recommended_steps": [
            "Create a pygame main loop with rendering and input handling.",
            "Implement gameplay state, scoring, and collision behavior.",
            "Run the game and validate controls, frame loop stability, and scoring logic.",
        ],
        "open_questions": [],
    }
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": __import__("json").dumps(payload)}]}},
    )
    result = runner.plan("Build me a pygame game from scratch")
    assert result.ready_to_execute is False


def test_repo_fix_with_validation_and_no_open_questions_is_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner(DummyClient(), tmp_path, model="demo")
    payload = {
        "task_summary": "Fix planner bug",
        "candidate_files": ["villani_code/state.py"],
        "assumptions": ["Need to inspect runtime behavior"],
        "recommended_steps": [
            "Implement the fix safely.",
            "Run validation and summarize results.",
            "Update tests accordingly.",
        ],
        "validation_approach": ["pytest tests/test_plan_workflow.py"],
        "open_questions": [],
    }
    monkeypatch.setattr(
        runner,
        "run",
        lambda *_a, **_k: {"response": {"content": [{"type": "text", "text": __import__("json").dumps(payload)}]}},
    )
    result = runner.plan("Fix the planning bug in this repo")
    assert result.ready_to_execute is True
