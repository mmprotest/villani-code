from pathlib import Path

from villani_code.state import Runner


class NoopClient:
    def create_message(self, payload, stream):
        return {"id": "1", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_render_stream_event_emits_stream_text_when_printing_disabled(
    tmp_path: Path, capsys
) -> None:
    events: list[dict] = []
    runner = Runner(
        client=NoopClient(),
        repo=tmp_path,
        model="m",
        stream=True,
        print_stream=False,
        event_callback=events.append,
    )

    runner._render_stream_event(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}}
    )
    runner._render_stream_event({"type": "message_stop"})

    captured = capsys.readouterr()
    assert captured.out == ""
    stream_text = [e["text"] for e in events if e.get("type") == "stream_text"]
    assert "hello" in "".join(stream_text)


def test_render_stream_event_emits_thinking_delta_when_printing_disabled(
    tmp_path: Path, capsys
) -> None:
    events: list[dict] = []
    runner = Runner(
        client=NoopClient(),
        repo=tmp_path,
        model="m",
        stream=True,
        print_stream=False,
        event_callback=events.append,
    )

    runner._render_stream_event(
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "chain"}}
    )
    runner._render_stream_event({"type": "message_stop"})

    captured = capsys.readouterr()
    assert captured.out == ""
    stream_text = [e["text"] for e in events if e.get("type") == "stream_text"]
    assert "chain" in "".join(stream_text)
