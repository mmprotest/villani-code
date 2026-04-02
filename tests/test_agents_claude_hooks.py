from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.agents.claude_hook_events import parse_hook_events_jsonl


def test_parse_hook_events_maps_to_adapter_events(tmp_path: Path) -> None:
    hook_path = tmp_path / "claude_hook_events.jsonl"
    hook_path.write_text(
        "\n".join(
            [
                '{"timestamp": 1.0, "hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": "src/app.py"}}',
                '{"timestamp": 2.0, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "pytest -q"}}',
                '{"timestamp": 3.0, "hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "error": "non-zero exit"}',
            ]
        ),
        encoding="utf-8",
    )

    parsed = parse_hook_events_jsonl(hook_path)

    event_types = [event.type for event in parsed.events]
    assert "write_file" in event_types
    assert "shell_command" in event_types
    assert "command_failed" in event_types
    write_event = next(event for event in parsed.events if event.type == "write_file")
    assert write_event.payload["path"] == "src/app.py"
