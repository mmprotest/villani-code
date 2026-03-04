from villani_code.turn_validation import repair_messages, validate_tool_turns


def test_validate_fails_when_tool_result_missing_after_tool_use() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "A", "name": "Ls", "input": {}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "next"}]},
    ]

    try:
        validate_tool_turns(messages)
    except ValueError as exc:
        assert "A" in str(exc)
        return
    raise AssertionError("expected ValueError")


def test_validate_fails_when_tool_result_not_immediately_after() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "A", "name": "Ls", "input": {}}]},
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "A", "content": "ok", "is_error": False}]},
    ]

    try:
        validate_tool_turns(messages)
    except ValueError as exc:
        assert "does not start with tool_result" in str(exc)
        return
    raise AssertionError("expected ValueError")


def test_validate_fails_when_tool_result_not_first() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "A", "name": "Ls", "input": {}}]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_result", "tool_use_id": "A", "content": "ok", "is_error": False},
            ],
        },
    ]

    try:
        validate_tool_turns(messages)
    except ValueError as exc:
        assert "does not start with tool_result" in str(exc)
        return
    raise AssertionError("expected ValueError")


def test_repair_merges_consecutive_assistants_then_validate_passes() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "prep"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "A", "name": "Ls", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "A", "content": "ok", "is_error": False}]},
    ]

    repaired = repair_messages(messages)
    assert len(repaired) == 2
    assert repaired[0]["role"] == "assistant"
    validate_tool_turns(repaired)
