from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
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
    first_payload = client.payloads[1]
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


def test_constrained_run_injects_task_contract_message(tmp_path: Path):
    class Client:
        def __init__(self):
            self.first_payload = None
            self.payloads = []

        def create_message(self, payload, stream):
            self.payloads.append(payload)
            if self.first_payload is None:
                self.first_payload = payload
            return {"id": "1", "role": "assistant", "content": [{"type": "text", "text": "done"}]}

    client = Client()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, small_model=True, plan_mode="off")
    runner.run("fix failing test in src/app.py")
    assert client.first_payload is not None
    runtime_payload = client.payloads[1] if hasattr(client, "payloads") else client.first_payload
    texts = [b.get("text", "") for m in runtime_payload["messages"] for b in m.get("content", []) if isinstance(b, dict) and b.get("type") == "text"]
    contract_lines = [t for t in texts if "Task contract" in t]
    assert contract_lines
    assert "name likely target file first" in contract_lines[-1]


def test_constrained_recovery_stages_then_terminates(tmp_path: Path):
    client = FakeClientStall()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, small_model=True, plan_mode="off")
    out = runner.run("inspect and fix")
    text_blocks = [b.get("text", "") for b in out["response"]["content"] if b.get("type") == "text"]
    assert any("Stopping due to constrained-run blocker" in t for t in text_blocks)
    all_msgs = [m for p in client.payloads for m in p.get("messages", [])]
    text_msgs = [b.get("text", "") for m in all_msgs for b in m.get("content", []) if isinstance(b, dict) and b.get("type") == "text"]
    assert any("RECOVERY MODE: State the single target file, the exact verification goal, and make exactly one next tool call." in t for t in text_msgs)
    assert any("RECOVERY MODE: Do not edit yet. In <=5 lines explain the blocker, inspect exactly one relevant file/diff, then either patch the locked target or finish." in t for t in text_msgs)


class FakeClientDiagnosisThenDone:
    def __init__(self, diagnosis_text: str):
        self.calls = 0
        self.diagnosis_text = diagnosis_text
        self.payloads = []

    def create_message(self, payload, stream):
        self.calls += 1
        self.payloads.append(payload)
        if self.calls == 1:
            return {"role": "assistant", "content": [{"type": "text", "text": self.diagnosis_text}]}
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_run_injects_diagnosis_hint_into_existing_prompt_context(tmp_path: Path):
    diag = '{"target_file":"src/app/config.py","bug_class":"wrong_precedence","fix_intent":"Prefer env over file values."}'
    client = FakeClientDiagnosisThenDone(diag)
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, small_model=True)
    runner.run("fix failing config precedence")
    assert len(client.payloads) >= 2
    first_runtime_payload = client.payloads[1]
    texts = [
        b.get("text", "")
        for m in first_runtime_payload["messages"]
        if m.get("role") == "user"
        for b in m.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    assert any("Likely diagnosis:" in t for t in texts)
    assert any("Bug class: wrong_precedence" in t for t in texts)


def test_invalid_diagnosis_falls_back_without_crashing(tmp_path: Path):
    client = FakeClientDiagnosisThenDone("not-json")
    events: list[dict] = []
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, small_model=True, event_callback=events.append)
    out = runner.run("fix bug")
    assert out["response"]["content"][0]["text"] == "done"
    assert any(e.get("type") == "diagnosis_attempted" for e in events)
    assert any(e.get("type") == "diagnosis_failed" for e in events)


class FakeClientDiagnosisThenReadThenDone:
    def __init__(self, diagnosis_text: str):
        self.calls = 0
        self.diagnosis_text = diagnosis_text
        self.payloads = []

    def create_message(self, payload, stream):
        self.calls += 1
        self.payloads.append(payload)
        if self.calls == 1:
            return {"role": "assistant", "content": [{"type": "text", "text": self.diagnosis_text}]}
        if self.calls == 2:
            return {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-2", "name": "Read", "input": {"file_path": "src/app/config.py"}}
                ],
            }
        return {
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        }


def test_diagnosis_target_forces_initial_runtime_read(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app" / "config.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE=1\n", encoding="utf-8")
    diag = '{"target_file":"src/app/config.py","bug_class":"wrong_precedence","fix_intent":"Prefer env over file values."}'
    events: list[dict] = []
    client = FakeClientDiagnosisThenReadThenDone(diag)
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, small_model=True, event_callback=events.append, benchmark_config=BenchmarkRuntimeConfig(enabled=True, task_id="diag-strong", allowlist_paths=["src/"], expected_files=["src/app/config.py"]))

    out = runner.run("fix config precedence")

    forced_read_events = [e for e in events if e.get("type") == "diagnosis_target_forced_read"]
    assert forced_read_events
    assert forced_read_events[-1]["enforced"] is True
    assert forced_read_events[-1]["target_file"] == "src/app/config.py"
    assert out["transcript"]["tool_invocations"][0]["name"] == "Read"
    assert out["transcript"]["tool_invocations"][0]["input"]["file_path"] == "src/app/config.py"


def test_forced_read_still_allows_normal_tool_loop_afterward(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app" / "config.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE=1\n", encoding="utf-8")
    diag = '{"target_file":"src/app/config.py","bug_class":"wrong_precedence","fix_intent":"Prefer env over file values."}'
    client = FakeClientDiagnosisThenReadThenDone(diag)
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, small_model=True, benchmark_config=BenchmarkRuntimeConfig(enabled=True, task_id="diag-strong-2", allowlist_paths=["src/"], expected_files=["src/app/config.py"]))

    out = runner.run("fix config precedence")

    read_calls = [i for i in out["transcript"]["tool_invocations"] if i["name"] == "Read"]
    assert len(read_calls) >= 2




def test_diagnosis_target_weak_confidence_stays_hint_only(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app" / "config.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE=1\n", encoding="utf-8")
    diag = '{"target_file":"src/app/config.py","bug_class":"wrong_precedence","fix_intent":"Prefer env over file values."}'
    events: list[dict] = []
    client = FakeClientDiagnosisThenReadThenDone(diag)
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, small_model=True, event_callback=events.append)

    out = runner.run("fix config precedence")

    forced_read_events = [e for e in events if e.get("type") == "diagnosis_target_forced_read"]
    assert forced_read_events
    assert forced_read_events[-1]["confidence"] == "weak"
    assert forced_read_events[-1]["enforced"] is False
    assert any(e.get("type") == "diagnosis_target_hint_only" for e in events)
    first_invocation = out["transcript"]["tool_invocations"][0]
    assert first_invocation["name"] == "Read"
    assert first_invocation.get("forced") is not True

def test_benchmark_prose_only_after_forced_read_terminates_early(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('x')\n", encoding="utf-8")
    diag = '{"target_file":"src/app.py","bug_class":"logic_error","fix_intent":"Read and patch minimal behavior."}'

    class Client:
        def __init__(self):
            self.calls = 0

        def create_message(self, payload, stream):
            self.calls += 1
            if self.calls == 1:
                return {"role": "assistant", "content": [{"type": "text", "text": diag}]}
            return {"role": "assistant", "content": [{"type": "text", "text": "plan only, no tools"}]}

    events: list[dict] = []
    cfg = BenchmarkRuntimeConfig(enabled=True, task_id="t1", allowlist_paths=["src/"], expected_files=["src/app.py"])
    runner = Runner(client=Client(), repo=tmp_path, model="m", stream=False, benchmark_config=cfg, event_callback=events.append)

    out = runner.run("fix benchmark bug")

    assert out["execution"]["terminated_reason"] == "benchmark_no_progress_after_forced_read"
    assert any(e.get("type") == "benchmark_no_progress_after_forced_read" for e in events)


def test_interactive_mode_keeps_recovery_prompts_after_forced_read(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('x')\n", encoding="utf-8")
    diag = '{"target_file":"src/app.py","bug_class":"logic_error","fix_intent":"Read and patch minimal behavior."}'

    class Client:
        def __init__(self):
            self.calls = 0

        def create_message(self, payload, stream):
            self.calls += 1
            if self.calls == 1:
                return {"role": "assistant", "content": [{"type": "text", "text": diag}]}
            return {"role": "assistant", "content": [{"type": "text", "text": "still planning"}]}

    events: list[dict] = []
    runner = Runner(client=Client(), repo=tmp_path, model="m", stream=False, small_model=True, event_callback=events.append)
    out = runner.run("fix bug")

    assert out["response"]["content"][0]["text"] == "still planning"
    assert any(e.get("type") == "diagnosis_target_forced_read" and e.get("enforced") is False for e in events)
    assert any(e.get("type") == "diagnosis_target_hint_only" for e in events)
    assert not any(e.get("type") == "benchmark_no_progress_after_forced_read" for e in events)
