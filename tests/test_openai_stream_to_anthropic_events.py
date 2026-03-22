from villani_code.openai_client import (
    convert_openai_response_to_anthropic,
    openai_stream_to_anthropic_events,
)
from villani_code.streaming import assemble_anthropic_stream


def test_openai_stream_text_events_to_anthropic():
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: [DONE]',
    ]

    events = list(openai_stream_to_anthropic_events(lines, model="gpt-test"))

    assert events[0]["type"] == "message_start"
    assert events[1]["type"] == "content_block_start"
    assert events[2]["type"] == "content_block_delta"
    assert events[2]["delta"]["text"] == "Hel"
    assert events[3]["type"] == "content_block_delta"
    assert events[3]["delta"]["text"] == "lo"
    assert events[-1]["type"] == "message_stop"


def test_openai_stream_tool_call_arguments_emit_input_json_delta():
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"Write","arguments":"{\\"file_path\\":\\"a"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":".txt\\",\\"content\\":\\"x\\"}"}}]}}]}',
        'data: [DONE]',
    ]

    events = list(openai_stream_to_anthropic_events(lines, model="gpt-test"))

    assert events[0]["type"] == "message_start"
    assert events[1] == {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "tool_use", "id": "call_1", "name": "Write", "input": {}},
    }
    assert events[2]["type"] == "content_block_delta"
    assert events[2]["delta"]["type"] == "input_json_delta"
    assert events[3]["type"] == "content_block_delta"
    assert events[3]["delta"]["partial_json"] == '.txt","content":"x"}'
    assert events[-2] == {"type": "content_block_stop", "index": 1}
    assert events[-1] == {"type": "message_stop"}


def test_openai_non_stream_response_preserves_usage():
    response = {
        "choices": [{"message": {"content": "Hello"}}],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        },
    }

    converted = convert_openai_response_to_anthropic(response)

    assert converted["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }


def test_openai_stream_usage_preserved_after_assembly():
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}],"usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18}}',
        'data: [DONE]',
    ]

    response = assemble_anthropic_stream(openai_stream_to_anthropic_events(lines, model="gpt-test"))

    assert response["content"][0]["text"] == "Hello"
    assert response["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
