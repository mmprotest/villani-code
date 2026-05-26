from __future__ import annotations

from villani_code.plan_session import PlanAnswer, PlanSessionResult
from villani_code.state import Runner
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

    def run(self, instruction: str, messages=None, execution_budget=None, approved_plan=None):
        self.calls.append(("run", instruction, messages, execution_budget, approved_plan))
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


class RunnerClientStub:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def create_message(self, payload, stream=True):
        del stream
        self.payloads.append(payload)
        return {"content": [{"type": "text", "text": "Applied minimal fix and validated."}]}


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

    name, instruction, messages, execution_budget, approved_plan = runner.calls[-1]
    assert name == "run"
    assert instruction == "follow-up"
    assert isinstance(messages, list)
    assert execution_budget is None
    assert approved_plan is None


def test_plan_and_execute_paths_use_canonical_runner_contract() -> None:
    app = ContractApp()
    runner = RecordingRunner()
    controller = RunnerController(runner, app)

    controller._replan_worker()
    controller._run_execute_plan_worker()
    controller._run_villani_mode_worker()

    assert ("plan", "plan task", app.plan_answers) in runner.calls
    assert any(call[0] == "run" and call[4] is not None for call in runner.calls)
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
        assert "plan" in str(exc)
    else:
        raise AssertionError("Expected missing method contract failure")


def test_runner_creates_task_outcome_contract_and_compatibility_dict(tmp_path) -> None:
    events: list[dict] = []
    client = RunnerClientStub()
    runner = Runner(
        client=client,
        repo=tmp_path,
        model="x",
        stream=False,
        print_stream=False,
        event_callback=events.append,
    )
    runner.run("Fix a bug in villani_code/state.py with minimal changes.")
    assert runner._task_outcome_contract is not None
    assert runner._task_outcome_contract.task_mode
    assert set(runner._task_contract.keys()) >= {
        "task_mode",
        "success_predicate",
        "preferred_targets",
        "no_go_paths",
    }
    contract_events = [event for event in events if event.get("type") == "task_outcome_contract_created"]
    assert contract_events
    payload = contract_events[-1]["contract"]
    assert payload["task_mode"] == runner._task_contract["task_mode"]

    reliability_events = [event for event in events if event.get("type") == "reliability_layer_loaded"]
    assert reliability_events
    assert reliability_events[-1]["version"] == "contract-progress-gate-v1"

    assert client.payloads
    first_messages = client.payloads[0]["messages"]
    assert len(first_messages) > 1 or any(
        "<task_outcome_contract>" in str(part.get("text", ""))
        for message in first_messages
        for part in (message.get("content", []) if isinstance(message.get("content", []), list) else [])
        if isinstance(part, dict)
    )
    assert any(
        "<task_outcome_contract>" in str(part.get("text", ""))
        for message in first_messages
        for part in (message.get("content", []) if isinstance(message.get("content", []), list) else [])
        if isinstance(part, dict)
    )
