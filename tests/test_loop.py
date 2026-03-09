from pathlib import Path

from villani_code.state import Runner


class FakeClient:
    def __init__(self):
        self.calls = 0

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-123", "name": "Ls", "input": {"path": "."}}
                ],
            }
        return {
            "id": "2",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        }


def test_loop_appends_tool_result_with_matching_id(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    client = FakeClient()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    result = runner.run("list files")

    tool_result_messages = [
        m for m in result["messages"] if m["role"] == "user" and m["content"][0].get("type") == "tool_result"
    ]
    assert tool_result_messages
    assert tool_result_messages[0]["content"][0]["tool_use_id"] == "tool-123"
    assert client.calls == 2


class FakeClientTwoTools:
    def __init__(self):
        self.calls = 0
        self.second_payload = None

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Ls", "input": {"path": "."}},
                    {"type": "tool_use", "id": "tool-2", "name": "Ls", "input": {"path": "."}},
                ],
            }
        self.second_payload = payload
        return {
            "id": "2",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        }


def test_loop_batches_multiple_tool_results_in_single_user_message(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    client = FakeClientTwoTools()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    runner.run("list files")

    assert client.second_payload is not None
    user_messages = [m for m in client.second_payload["messages"] if m["role"] == "user" and m["content"] and m["content"][0].get("type") == "tool_result"]
    assert len(user_messages) == 1
    assert [b["tool_use_id"] for b in user_messages[0]["content"]] == ["tool-1", "tool-2"]


def test_runner_emits_tool_use_before_tool_execution(tmp_path: Path):
    class ToolThenDoneClient:
        def __init__(self):
            self.calls = 0

        def create_message(self, payload, stream):
            self.calls += 1
            if self.calls == 1:
                return {
                    "id": "1",
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tool-1", "name": "Ls", "input": {"path": "."}}],
                }
            return {"id": "2", "role": "assistant", "content": [{"type": "text", "text": "done"}]}

    events: list[dict] = []
    runner = Runner(client=ToolThenDoneClient(), repo=tmp_path, model="m", stream=False, event_callback=events.append)

    runner.run("list files")

    assert [event["type"] for event in events if event["type"] in {"tool_use", "tool_started", "tool_result"}] == [
        "tool_use",
        "tool_started",
        "tool_result",
    ]
    tool_use_event = next(event for event in events if event["type"] == "tool_use")
    assert tool_use_event["name"] == "Ls"
    assert tool_use_event["input"] == {"path": "."}


class FakeClientEmptyThenDone:
    def __init__(self):
        self.calls = 0
        self.second_payload = None

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [],
                "stop_reason": "end_turn",
            }
        self.second_payload = payload
        return {
            "id": "2",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok continuing"}],
            "stop_reason": "end_turn",
        }


class FakeClientToolUseThenDone:
    def __init__(self):
        self.calls = 0
        self.second_payload = None

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            return {
                "id": "1",
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Ls", "input": {"path": "."}}],
            }
        self.second_payload = payload
        return {
            "id": "2",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        }


class FakeClientTwoEmptyThenDone:
    def __init__(self):
        self.calls = 0

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls <= 2:
            return {
                "id": str(self.calls),
                "role": "assistant",
                "content": [],
                "stop_reason": "end_turn",
            }
        return {
            "id": "3",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok after two empties"}],
            "stop_reason": "end_turn",
        }


class FakeClientThreeEmpty:
    def __init__(self):
        self.calls = 0

    def create_message(self, payload, stream):
        self.calls += 1
        return {
            "id": str(self.calls),
            "role": "assistant",
            "content": [],
            "stop_reason": "end_turn",
        }


def test_loop_retries_on_empty_assistant_turn(tmp_path: Path):
    client = FakeClientEmptyThenDone()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    result = runner.run("whatever")

    assert client.calls == 2
    assert result["response"]["content"] == [{"type": "text", "text": "ok continuing"}]
    assert client.second_payload is not None
    continuation_messages = [
        m
        for m in client.second_payload["messages"]
        if m["role"] == "user"
        and m["content"]
        and "Continue. You ended your previous turn with no output." in m["content"][0].get("text", "")
    ]
    assert continuation_messages


def test_tool_result_followup_is_pure_tool_result_message(tmp_path: Path):
    client = FakeClientToolUseThenDone()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    original_execute = runner._execute_tool_with_policy

    def _execute_and_set_pending(tool_name, tool_input, tool_use_id, message_count):
        result = original_execute(tool_name, tool_input, tool_use_id, message_count)
        runner._pending_verification = "<verification>combined</verification>"
        return result

    runner._execute_tool_with_policy = _execute_and_set_pending
    runner.run("list files")

    assert client.second_payload is not None
    first_tool_result_msg = next(
        m
        for m in client.second_payload["messages"]
        if m["role"] == "user" and m["content"] and m["content"][0].get("type") == "tool_result"
    )
    assistant_idx = next(
        i
        for i, m in enumerate(client.second_payload["messages"])
        if m["role"] == "assistant" and m["content"] and m["content"][0].get("type") == "tool_use"
    )
    user_follow_ups = [m for m in client.second_payload["messages"][assistant_idx + 1 :] if m["role"] == "user"]
    assert len(user_follow_ups) == 1
    assert first_tool_result_msg == user_follow_ups[0]
    assert all(block.get("type") == "tool_result" for block in first_tool_result_msg["content"])
    assert "<verification>combined</verification>" in first_tool_result_msg["content"][-1]["content"]


def test_anthropic_message_order_after_tool_use_with_pending_verification(tmp_path: Path):
    client = FakeClientToolUseThenDone()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    original_execute = runner._execute_tool_with_policy

    def _execute_and_set_pending(tool_name, tool_input, tool_use_id, message_count):
        result = original_execute(tool_name, tool_input, tool_use_id, message_count)
        runner._pending_verification = "<verification>order-check</verification>"
        return result

    runner._execute_tool_with_policy = _execute_and_set_pending
    runner.run("list files")

    assert client.second_payload is not None
    msgs = client.second_payload["messages"]
    assistant_idx = next(
        i
        for i, m in enumerate(msgs)
        if m["role"] == "assistant" and m["content"] and m["content"][0].get("type") == "tool_use"
    )
    follow_up_messages = msgs[assistant_idx + 1 :]
    user_follow_ups = [m for m in follow_up_messages if m["role"] == "user"]
    assert len(user_follow_ups) == 1

    next_user_message = user_follow_ups[0]
    assert next_user_message == msgs[assistant_idx + 1]
    assert next_user_message["content"][0]["type"] == "tool_result"
    assert all(block.get("type") == "tool_result" for block in next_user_message["content"])
    assert "<verification>order-check</verification>" in next_user_message["content"][-1]["content"]

def test_loop_retries_twice_then_succeeds(tmp_path: Path):
    client = FakeClientTwoEmptyThenDone()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    result = runner.run("whatever")

    assert client.calls == 3
    assert result["response"]["content"] == [{"type": "text", "text": "ok after two empties"}]


def test_loop_stops_after_retry_limit_on_empty_turns(tmp_path: Path):
    client = FakeClientThreeEmpty()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)

    result = runner.run("whatever")

    assert client.calls == 3
    assert result["response"]["content"] == []


class FakeClientDiffProposal:
    def __init__(self):
        self.calls = 0

    def create_message(self, payload, stream):
        self.calls += 1
        return {
            "id": "1",
            "role": "assistant",
            "content": [{"type": "text", "text": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"}],
        }


def test_loop_captures_diff_proposal(tmp_path: Path):
    (tmp_path / "a.txt").write_text("old\n", encoding="utf-8")
    runner = Runner(client=FakeClientDiffProposal(), repo=tmp_path, model="m", stream=False)
    runner.run("propose")
    proposals = runner.proposals.list()
    assert proposals
    assert proposals[0].files_touched == ["a.txt"]


class FakeClientStall:
    def __init__(self):
        self.calls = 0
        self.payloads = []

    def create_message(self, payload, stream):
        self.calls += 1
        self.payloads.append(payload)
        return {"id": str(self.calls), "role": "assistant", "content": [{"type": "text", "text": "ok"}]}


def test_stall_recovery_injects_instruction(tmp_path: Path):
    runner = Runner(client=FakeClientStall(), repo=tmp_path, model="m", stream=False)
    runner.run("x")
    found = False
    for p in runner.client.payloads[1:]:
        for m in p["messages"]:
            if m["role"] == "user" and m["content"] and "RECOVERY MODE" in m["content"][0].get("text", ""):
                found = True
    assert found


def test_stall_final_stop_after_recovery_attempts(tmp_path: Path):
    runner = Runner(client=FakeClientStall(), repo=tmp_path, model="m", stream=False)
    result = runner.run("x")
    text_blocks = [b.get("text","") for b in result["response"]["content"] if b.get("type")=="text"]
    assert any("still blocked" in t for t in text_blocks)


def test_stall_recovery_messages_are_narrow_and_staged(tmp_path: Path):
    runner = Runner(client=FakeClientStall(), repo=tmp_path, model="m", stream=False)
    runner.run("x")
    recovery_messages = []
    for p in runner.client.payloads[1:]:
        for m in p["messages"]:
            if m["role"] == "user" and m["content"] and "RECOVERY MODE" in m["content"][0].get("text", ""):
                recovery_messages.append(m["content"][0]["text"])
    assert any("single target file" in msg for msg in recovery_messages)
    assert any("Do not edit yet" in msg for msg in recovery_messages)


def test_small_model_run_injects_task_contract_steering_message(tmp_path: Path):
    client = FakeClientStall()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, small_model=True)
    runner.run("fix failing test in src/foo.py")
    first_payload = client.payloads[0]
    user_texts = [
        b.get("text", "")
        for m in first_payload["messages"]
        if m.get("role") == "user"
        for b in m.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    assert any("Task contract" in t and "name likely target file first" in t for t in user_texts)


def test_stall_in_villani_mode_terminates_without_relax_prompt(tmp_path: Path):
    runner = Runner(client=FakeClientStall(), repo=tmp_path, model="m", stream=False, villani_mode=True)
    result = runner.run("x")
    text_blocks = [b.get("text", "") for b in result["response"]["content"] if b.get("type") == "text"]
    joined = "\n".join(text_blocks)
    assert "constraint should I relax" not in joined
    assert "Stopping due to constrained-run blocker" in joined
    assert "Success predicate:" in joined
