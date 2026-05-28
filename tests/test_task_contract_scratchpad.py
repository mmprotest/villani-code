from pathlib import Path

from villani_code.state import Runner


class ContractClient:
    def __init__(self, runtime_responses):
        self.calls = 0
        self.payloads = []
        self.runtime_responses = list(runtime_responses)

    def create_message(self, payload, stream):
        self.calls += 1
        self.payloads.append(payload)
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": [{"type": "text", "text": "TASK CONTRACT CHECKLIST\n\nRequired outputs:\n- Update file.\n\nRequired behaviours:\n- Keep behavior stable.\n\nRequired constraints:\n- Keep scope minimal.\n\nValidation signals:\n- Run direct verification."}],
            }
        if self.runtime_responses:
            return self.runtime_responses.pop(0)
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


def _user_texts(payload):
    return [
        b.get("text", "")
        for m in payload.get("messages", [])
        if m.get("role") == "user"
        for b in m.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    ]


def test_contract_generation_and_first_turn_injection(tmp_path: Path):
    client = ContractClient(runtime_responses=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}, {"role": "assistant", "content": [{"type": "text", "text": "done final"}]}])
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)
    runner.run("Create file a.txt with hello")
    assert runner.task_contract_checklist is not None
    assert "TASK CONTRACT CHECKLIST" in runner.task_contract_checklist
    first_runtime_payload = client.payloads[1]
    assert any("TASK CONTRACT CHECKLIST" in t for t in _user_texts(first_runtime_payload))


def test_reminder_injected_after_8_tool_calls(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    runtime = []
    for i in range(8):
        runtime.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"file_path": "a.txt"}}]})
    runtime.extend([{"role": "assistant", "content": [{"type": "text", "text": "done"}]}, {"role": "assistant", "content": [{"type": "text", "text": "done final"}]}])
    client = ContractClient(runtime_responses=runtime)
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)
    runner.run("Inspect a.txt")
    assert any("TASK CONTRACT REMINDER" in t for payload in client.payloads for t in _user_texts(payload))


def test_no_reminder_before_8_tool_calls(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    runtime = []
    for i in range(3):
        runtime.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"file_path": "a.txt"}}]})
    runtime.extend([{"role": "assistant", "content": [{"type": "text", "text": "done"}]}, {"role": "assistant", "content": [{"type": "text", "text": "done final"}]}])
    client = ContractClient(runtime_responses=runtime)
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)
    runner.run("Inspect a.txt")
    assert not any("TASK CONTRACT REMINDER" in t for payload in client.payloads for t in _user_texts(payload))


def test_finalization_reminder_injected(tmp_path: Path):
    client = ContractClient(runtime_responses=[{"role": "assistant", "content": [{"type": "text", "text": "ready to finish"}]}, {"role": "assistant", "content": [{"type": "text", "text": "finalized"}]}])
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False)
    runner.run("Do a quick check")
    assert any("TASK CONTRACT FINAL CHECK" in t for payload in client.payloads for t in _user_texts(payload))


class FailingContractClient:
    def __init__(self):
        self.calls = 0

    def create_message(self, payload, stream):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("contract generation failed")
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_generation_failure_uses_fallback_and_continues(tmp_path: Path):
    events = []
    runner = Runner(client=FailingContractClient(), repo=tmp_path, model="m", stream=False, event_callback=events.append)
    out = runner.run("Any task")
    assert runner.task_contract_checklist is not None
    assert "Check the original task for required files or artifacts." in runner.task_contract_checklist
    assert out["response"]["content"][0]["text"] == "done"
    assert any(e.get("type") == "task_contract_generation_failed" for e in events)
