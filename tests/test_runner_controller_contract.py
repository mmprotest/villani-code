from __future__ import annotations

from villani_code.plan_session import PlanAnswer, PlanSessionResult
from villani_code.tui.controller import RunnerController


class ContractApp:
    def __init__(self) -> None:
        self.messages: list[object] = []
        self.plan_instruction = "plan task"
        self.plan_answers: list[PlanAnswer] = [PlanAnswer("q1", "a")]
        self.ready_plan = PlanSessionResult(instruction="task", task_summary="sum", ready_to_execute=True)

    def post_message(self, message: object) -> object:
        self.messages.append(message)
        return message

    def call_from_thread(self, callback, *args, **kwargs):
        return callback(*args, **kwargs)

    def apply_plan_result(self, _result: PlanSessionResult, _reset_answers: bool) -> None:
        return None

    def record_plan_answer(self, answer: PlanAnswer) -> None:
        self.plan_answers.append(answer)

    def get_plan_instruction(self) -> str:
        return self.plan_instruction

    def get_plan_answers(self) -> list[PlanAnswer]:
        return list(self.plan_answers)

    def get_last_ready_plan(self) -> PlanSessionResult | None:
        return self.ready_plan


class RecordingRunner:
    def __init__(self) -> None:
        self.print_stream = True
        self.approval_callback = None
        self.event_callback = None
        self.permissions = None
        self.calls: list[tuple] = []

    def run(self, instruction: str, messages=None, execution_budget=None):
        self.calls.append(("run", instruction, messages, execution_budget))
        return {"response": {"content": []}}

    def plan(self, instruction: str, answers=None):
        self.calls.append(("plan", instruction, answers))
        return PlanSessionResult(instruction=instruction, task_summary="summary", ready_to_execute=True)

    def run_with_plan(self, plan: PlanSessionResult):
        self.calls.append(("run_with_plan", plan))
        return {"response": {"content": []}}

    def run_villani_mode(self):
        self.calls.append(("run_villani_mode",))
        return {"response": {"content": []}}


def test_minimal_contract_runner_works() -> None:
    app = ContractApp()
    runner = RecordingRunner()
    controller = RunnerController(runner, app)
    controller._run_prompt_worker("hello")
    assert runner.calls[0][0] == "run"


def test_run_prompt_passes_messages_only_through_runner_run() -> None:
    app = ContractApp()
    runner = RecordingRunner()
    controller = RunnerController(runner, app)

    controller._session_messages = [{"role": "assistant", "content": [{"type": "text", "text": "history"}]}]
    controller._run_prompt_worker("follow-up")

    name, instruction, messages, execution_budget = runner.calls[-1]
    assert name == "run"
    assert instruction == "follow-up"
    assert isinstance(messages, list)
    assert execution_budget is None


def test_plan_and_execute_paths_use_canonical_runner_contract() -> None:
    app = ContractApp()
    runner = RecordingRunner()
    controller = RunnerController(runner, app)

    controller._replan_worker()
    controller._run_execute_plan_worker()
    controller._run_villani_mode_worker()

    assert ("plan", "plan task", app.plan_answers) in runner.calls
    assert any(call[0] == "run_with_plan" for call in runner.calls)
    assert any(call[0] == "run_villani_mode" for call in runner.calls)


def test_missing_required_runner_method_fails_early() -> None:
    app = ContractApp()

    class BrokenRunner:
        print_stream = False
        approval_callback = None
        event_callback = None
        permissions = None

        def run(self, instruction: str, messages=None, execution_budget=None):
            return {"response": {"content": []}}

    try:
        RunnerController(BrokenRunner(), app)
    except TypeError as exc:
        assert "run_with_plan" in str(exc)
    else:
        raise AssertionError("Expected missing method contract failure")
