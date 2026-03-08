from __future__ import annotations

from typing import Any

from villani_code import cli


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[Any] = []

    def print(self, value: Any) -> None:
        self.lines.append(value)


def test_print_response_text_blocks_handles_all_malformed_shapes(monkeypatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(cli, "console", recorder)

    malformed_inputs = [
        None,
        "not-a-dict",
        1,
        {"response": None},
        {"response": []},
        {"response": {"content": None}},
        {"response": {"content": {"type": "text", "text": "x"}}},
        {"response": {"content": [123, object(), {"text": "missing-type"}, {"type": "text", "text": 7}, {"type": "json", "text": "skip"}]}}
    ]

    for item in malformed_inputs:
        cli._print_response_text_blocks(item)

    assert recorder.lines == []


def test_print_response_text_blocks_prints_valid_text_blocks(monkeypatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(cli, "console", recorder)

    cli._print_response_text_blocks(
        {
            "response": {
                "content": [
                    "hello",
                    {"type": "text", "text": "world"},
                    {"type": "text", "text": "!"},
                    {"type": "tool_use", "name": "noop"},
                ]
            }
        }
    )

    assert recorder.lines == ["hello", "world", "!"]


def test_print_response_text_blocks_prints_plain_response_and_content_strings(monkeypatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(cli, "console", recorder)

    cli._print_response_text_blocks({"response": "plain response"})
    cli._print_response_text_blocks({"response": {"content": "plain content"}})
    cli._print_response_text_blocks({"content": "top-level content"})

    assert recorder.lines == ["plain response", "plain content", "top-level content"]


def test_print_response_text_blocks_never_raises_when_console_print_fails(monkeypatch) -> None:
    class _ExplodingConsole:
        def print(self, _value: Any) -> None:
            raise RuntimeError("render failed")

    monkeypatch.setattr(cli, "console", _ExplodingConsole())

    cli._print_response_text_blocks({"response": {"content": ["boom"]}})
