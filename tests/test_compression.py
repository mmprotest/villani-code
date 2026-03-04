from villani_code.compression import compress_messages, estimate_size
from villani_code.turn_validation import validate_tool_turns


def test_compression_preserves_tool_ordering_and_reduces_size():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "x" * 5000}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "a"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "is_error": False, "content": "y" * 5000}]},
        {"role": "assistant", "content": [{"type": "text", "text": "z" * 5000}]},
    ]
    original = estimate_size(messages)
    compressed = compress_messages(messages, max_chars=1000, keep_recent_turns=1)
    validate_tool_turns(compressed)
    assert estimate_size(compressed) < original
    tool_result = compressed[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "a"


def test_compression_noop_when_under_limit():
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert compress_messages(messages, max_chars=100) == messages


def test_compression_keeps_tool_use_and_result_together_at_recent_boundary():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "old" * 1000}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "r" * 2000}]},
        {"role": "assistant", "content": [{"type": "text", "text": "new"}]},
    ]
    compressed = compress_messages(messages, max_chars=100, keep_recent_turns=2)
    validate_tool_turns(compressed)
    assert compressed[1]["content"][0]["type"] == "tool_use"
    assert compressed[2]["content"][0]["type"] == "tool_result"
