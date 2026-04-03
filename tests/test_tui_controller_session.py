from __future__ import annotations

from villani_code.tui.controller import RunnerController


class DummyApp:
    def post_message(self, message: object) -> object:
        return message


class SessionRunner:
    def __init__(self) -> None:
        self.print_stream = False
        self.approval_callback = None
        self.event_callback = None
        self.permissions = None
        self.calls: list[dict[str, object]] = []

    def run(self, instruction: str, messages=None, execution_budget=None, approved_plan=None):
        _ = execution_budget
        _ = approved_plan
        self.calls.append({"instruction": instruction, "messages": messages})
        if messages is None:
            session_messages = [
                {"role": "system", "content": [{"type": "text", "text": "sys"}]},
                {"role": "user", "content": [{"type": "text", "text": instruction}]},
                {"role": "assistant", "content": [{"type": "text", "text": "first"}]},
            ]
        else:
            session_messages = [*messages, {"role": "assistant", "content": [{"type": "text", "text": "next"}]}]
        return {
            "response": {"content": [{"type": "text", "text": "ok"}]},
            "messages": session_messages,
        }

    def plan(self, instruction: str, answers=None):
        _ = (instruction, answers)
        raise RuntimeError("unused")

    def run_with_plan(self, plan):
        _ = plan
        return {"response": {"content": []}}

    def run_villani_mode(self):
        return {"response": {"content": []}}


def test_run_prompt_persists_messages_across_turns() -> None:
    app = DummyApp()
    runner = SessionRunner()
    controller = RunnerController(runner, app)

    controller._run_prompt_worker("first prompt")
    assert runner.calls[0]["messages"] is None
    assert controller._session_messages is not None

    controller._run_prompt_worker("follow up")
    sent = runner.calls[1]["messages"]
    assert isinstance(sent, list)
    assert sent[-1]["role"] == "user"
    assert sent[-1]["content"][0]["text"] == "follow up"
    assert controller._session_messages is not None
    assert controller._session_messages[-1]["role"] == "assistant"


def test_reset_session_context_clears_history() -> None:
    app = DummyApp()
    runner = SessionRunner()
    controller = RunnerController(runner, app)

    controller._run_prompt_worker("first prompt")
    assert controller._session_messages is not None

    controller.reset_session_context()
    assert controller._session_messages is None


def test_fork_session_context_returns_deep_copy() -> None:
    app = DummyApp()
    runner = SessionRunner()
    controller = RunnerController(runner, app)

    controller._run_prompt_worker("first prompt")
    forked = controller.fork_session_context()
    assert forked == controller._session_messages
    assert forked is not controller._session_messages
