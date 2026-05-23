from villani_code.openai_client import openai_stream_to_anthropic_events


def test_openai_stream_text_events_to_anthropic():
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        "data: [DONE]",
    ]

    events = list(openai_stream_to_anthropic_events(lines, model="gpt-test"))

    assert events[0]["type"] == "message_start"
    assert events[1]["type"] == "content_block_start"
    assert events[2]["type"] == "content_block_delta"
    assert events[2]["delta"]["text"] == "Hel"
    assert events[3]["type"] == "content_block_delta"
    assert events[3]["delta"]["text"] == "lo"
    assert events[-1]["type"] == "message_stop"


def test_openai_stream_reasoning_events_to_anthropic_thinking_block():
    lines = [
        'data: {"choices":[{"delta":{"reasoning":"plan "}}]}',
        'data: {"choices":[{"delta":{"reasoning":"step"}}]}',
        'data: {"choices":[{"delta":{"content":"done"}}]}',
        "data: [DONE]",
    ]

    events = list(openai_stream_to_anthropic_events(lines, model="gpt-test"))

    assert events[0]["type"] == "message_start"
    assert events[1] == {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": ""},
    }
    assert events[2] == {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "plan "},
    }
    assert events[3] == {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "step"},
    }
    assert events[4] == {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "text", "text": ""},
    }
    assert events[5] == {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "text_delta", "text": "done"},
    }
    assert events[-3] == {"type": "content_block_stop", "index": 0}
    assert events[-2] == {"type": "content_block_stop", "index": 1}
    assert events[-1]["type"] == "message_stop"


def test_openai_stream_tool_call_arguments_emit_input_json_delta():
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"Write","arguments":"{\\"file_path\\":\\"a"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":".txt\\",\\"content\\":\\"x\\"}"}}]}}]}',
        "data: [DONE]",
    ]

    events = list(openai_stream_to_anthropic_events(lines, model="gpt-test"))

    assert events[0]["type"] == "message_start"
    assert events[1] == {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "tool_use", "id": "call_1", "name": "Write", "input": {}},
    }
    assert events[2]["type"] == "content_block_delta"
    assert events[2]["delta"]["type"] == "input_json_delta"
    assert events[3]["type"] == "content_block_delta"
    assert events[3]["delta"]["partial_json"] == '.txt","content":"x"}'
    assert events[-2] == {"type": "content_block_stop", "index": 0}
    assert events[-1] == {"type": "message_stop"}
