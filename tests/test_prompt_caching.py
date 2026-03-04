from pathlib import Path

from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.tools import tool_specs


def test_payload_contains_cache_control_on_system_blocks(tmp_path: Path):
    blocks = build_system_blocks(tmp_path)
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert blocks[-1]["text"] == "Cache checkpoint."


def test_payload_contains_cache_control_on_initial_reminder(tmp_path: Path):
    messages = build_initial_messages(tmp_path, "do work")
    content = messages[0]["content"]
    assert content[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in content[-1]


def test_payload_contains_cache_control_on_last_tool_spec():
    specs = tool_specs()
    assert specs[-1]["cache_control"] == {"type": "ephemeral"}
