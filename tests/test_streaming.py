from villani_code.streaming import assemble_anthropic_stream, parse_sse_events


def test_parse_and_assemble_stream_blocks():
    lines = [
        'data: {"type":"message_start","message":{"id":"m1","role":"assistant"}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking"}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"a"}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"b"}}',
        '{"type":"content_block_stop","index":0}',
        '{"type":"content_block_start","index":1,"content_block":{"type":"text"}}',
        '{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"hel"}}',
        '{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"lo"}}',
        '{"type":"content_block_stop","index":1}',
        '{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":1}}',
        '{"type":"message_stop"}',
    ]
    events = list(parse_sse_events(lines))
    msg = assemble_anthropic_stream(events)

    assert msg["content"][0]["thinking"] == "ab"
    assert msg["content"][1]["text"] == "hello"
    assert msg["stop_reason"] == "end_turn"
    assert msg["usage"]["input_tokens"] == 1
