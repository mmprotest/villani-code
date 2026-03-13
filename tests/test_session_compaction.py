from __future__ import annotations

from villani_code.context_budget import ContextBudget
from villani_code.state_runtime import validate_anthropic_tool_sequence
from villani_code.tui.controller import RunnerController


class DummyApp:
    def post_message(self, message: object) -> object:
        return message


class CapturingRunner:
    def __init__(self) -> None:
        self.print_stream = False
        self.approval_callback = None
        self.event_callback = None
        self.permissions = None
        self.last_messages = None

    def run(self, instruction: str, messages=None):
        self.last_messages = messages
        return {
            "response": {"content": [{"type": "text", "text": "ack"}]},
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "sys"}]},
                {"role": "user", "content": [{"type": "text", "text": "remember tool output please"}]},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "read a.py", "is_error": False}],
                },
                {"role": "assistant", "content": [{"type": "text", "text": "I read a.py"}]},
            ],
        }


def test_compaction_preserves_valid_tool_sequence_with_summary() -> None:
    budget = ContextBudget(max_chars=260, keep_last_turns=1)
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "system"}]},
        {"role": "user", "content": [{"type": "text", "text": "Objective: fix failing test"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "tests/test_x.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok", "is_error": False}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Found issue in parser.py"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-2", "name": "Patch", "input": {"file_path": "villani_code/parser.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-2", "content": "patched", "is_error": False}]},
    ]

    compacted = budget.compact_session_messages(messages)
    validate_anthropic_tool_sequence(compacted)

    summary = next((m for m in compacted if m.get("role") == "user" and isinstance(m.get("content"), list) and m["content"][0].get("type") == "text" and "[session summary" in m["content"][0].get("text", "")), None)
    assert summary is not None


def test_follow_up_prompt_can_include_prior_tool_and_assistant_context() -> None:
    runner = CapturingRunner()
    controller = RunnerController(runner, DummyApp())

    controller._run_prompt_worker("first")
    controller._run_prompt_worker("follow-up: use earlier file read")

    sent = runner.last_messages
    assert isinstance(sent, list)
    assert any(
        isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
        for m in sent
    )
    assert sent[-1]["role"] == "user"
    assert "follow-up" in sent[-1]["content"][0]["text"]
