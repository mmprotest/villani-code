import json

from villani_code.openai_client import openai_stream_to_anthropic_events
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



def test_pi_proxy_single_chunk_stream_assembles_text():
    chunk = {
        "id": "pi-villani-1700000000000",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "pi-test",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    lines = [f"data: {json.dumps(chunk)}", "data: [DONE]"]

    response = assemble_anthropic_stream(openai_stream_to_anthropic_events(lines, model="pi-test"))

    assert response["content"][0]["text"] == "hello"


def test_pi_proxy_single_chunk_stream_assembles_tool_calls():
    chunk = {
        "id": "pi-villani-1700000000000",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "pi-test",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "Write", "arguments": '{"file_path":"a.txt"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    lines = [f"data: {json.dumps(chunk)}", "data: [DONE]"]

    response = assemble_anthropic_stream(openai_stream_to_anthropic_events(lines, model="pi-test"))

    assert response["content"][1]["type"] == "tool_use"
    assert response["content"][1]["name"] == "Write"
    assert response["content"][1]["input"] == {"file_path": "a.txt"}
