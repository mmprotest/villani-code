import json

from villani_code.openai_client import convert_messages_to_openai, convert_tools_to_openai


def test_convert_tools_to_openai_format():
    anthropic_tools = [
        {
            "name": "Write",
            "description": "Write file",
            "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}},
        }
    ]

    tools = convert_tools_to_openai(anthropic_tools)

    assert tools == [
        {
            "type": "function",
            "function": {
                "name": "Write",
                "description": "Write file",
                "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}},
            },
        }
    ]


def test_convert_anthropic_messages_to_openai_messages():
    payload = {
        "system": [{"type": "text", "text": "You are helpful."}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Do thing"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Running tool"},
                    {"type": "tool_use", "id": "call_1", "name": "Write", "input": {"file_path": "a.txt"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "ok", "is_error": False},
                ],
            },
        ],
    }

    converted = convert_messages_to_openai(payload)

    assert converted[0] == {"role": "system", "content": "You are helpful."}
    assert converted[1] == {"role": "user", "content": "Do thing"}
    assert converted[2]["role"] == "assistant"
    assert converted[2]["content"] == "Running tool"
    assert converted[2]["tool_calls"][0]["id"] == "call_1"
    assert converted[2]["tool_calls"][0]["function"]["name"] == "Write"
    assert json.loads(converted[2]["tool_calls"][0]["function"]["arguments"]) == {"file_path": "a.txt"}
    assert converted[3] == {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
