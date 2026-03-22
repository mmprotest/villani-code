from pathlib import Path

from villani_code.state import Runner


class NoopClient:
    def create_message(self, payload, stream):
        return {"id": "1", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_render_stream_event_emits_stream_text_when_printing_disabled(tmp_path: Path, capsys) -> None:
    events: list[dict] = []
    runner = Runner(
        client=NoopClient(),
        repo=tmp_path,
        model="m",
        stream=True,
        print_stream=False,
        event_callback=events.append,
    )

    runner._render_stream_event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}})
    runner._render_stream_event({"type": "message_stop"})

    captured = capsys.readouterr()
    assert captured.out == ""
    stream_text = [e["text"] for e in events if e.get("type") == "stream_text"]
    assert "hello" in "".join(stream_text)


def test_runner_emits_model_request_finished_with_nonstream_usage(tmp_path: Path) -> None:
    events: list[dict] = []

    class UsageClient:
        def create_message(self, payload, stream):
            assert stream is False
            return {
                "id": "1",
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13},
            }

    runner = Runner(client=UsageClient(), repo=tmp_path, model="m", stream=False, event_callback=events.append)

    runner.run("say done")

    assert {
        "type": "model_request_finished",
        "model": "m",
        "usage": {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13},
    } in events


def test_runner_emits_model_request_finished_with_stream_usage(tmp_path: Path) -> None:
    events: list[dict] = []

    class StreamingUsageClient:
        def create_message(self, payload, stream):
            assert stream is True
            return iter(
                [
                    {
                        "type": "message_start",
                        "message": {"id": "1", "role": "assistant", "content": []},
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "done"},
                    },
                    {
                        "type": "content_block_stop",
                        "index": 0,
                    },
                    {
                        "type": "message_delta",
                        "delta": {},
                        "usage": {"input_tokens": 6, "output_tokens": 2, "total_tokens": 8},
                    },
                    {"type": "message_stop"},
                ]
            )

    runner = Runner(
        client=StreamingUsageClient(),
        repo=tmp_path,
        model="m",
        stream=True,
        print_stream=False,
        event_callback=events.append,
    )

    runner.run("say done")

    assert {
        "type": "model_request_finished",
        "model": "m",
        "usage": {"input_tokens": 6, "output_tokens": 2, "total_tokens": 8},
    } in events
