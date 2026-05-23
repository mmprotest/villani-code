from __future__ import annotations

from villani_code.openai_client import (
    build_openai_payload,
    convert_openai_response_to_anthropic,
    openai_stream_to_anthropic_events,
)


def test_streaming_payload_includes_stream_options_with_usage() -> None:
    payload = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "max_tokens": 128,
    }
    out = build_openai_payload(payload, stream=True)
    assert out["stream"] is True
    assert out["stream_options"] == {"include_usage": True}


def test_non_stream_payload_does_not_include_stream_options() -> None:
    payload = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "max_tokens": 128,
    }
    out = build_openai_payload(payload, stream=False)
    assert out["stream"] is False
    assert "stream_options" not in out


def test_openai_stream_propagates_usage_and_stop_reason() -> None:
    lines = [
        'data: {"choices":[{"delta":{"content":"Hi"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":101,"completion_tokens":22,"total_tokens":123}}',
        "data: [DONE]",
    ]
    events = list(openai_stream_to_anthropic_events(lines, model="gpt-test"))
    message_stop = events[-1]
    assert message_stop["type"] == "message_stop"
    assert message_stop["usage"] == {
        "prompt_tokens": 101,
        "completion_tokens": 22,
        "total_tokens": 123,
    }
    assert message_stop["stop_reason"] == "end_turn"


def test_openai_stream_maps_tool_call_finish_reason() -> None:
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"Write","arguments":"{\\"x\\":"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    events = list(openai_stream_to_anthropic_events(lines, model="gpt-test"))
    assert events[-1]["type"] == "message_stop"
    assert events[-1]["stop_reason"] == "tool_use"


def test_convert_openai_response_preserves_usage_id_model_and_stop_reason() -> None:
    response = {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Done"},
            }
        ],
    }
    converted = convert_openai_response_to_anthropic(response)
    assert converted["id"] == "chatcmpl-1"
    assert converted["model"] == "gpt-4o-mini"
    assert converted["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert converted["stop_reason"] == "end_turn"


def test_convert_openai_response_maps_reasoning_to_thinking_block() -> None:
    response = {
        "id": "chatcmpl-2",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "reasoning": "step-by-step",
                    "content": "Final answer",
                },
            }
        ],
    }

    converted = convert_openai_response_to_anthropic(response)

    assert converted["content"][0] == {"type": "thinking", "thinking": "step-by-step"}
    assert converted["content"][1] == {"type": "text", "text": "Final answer"}
