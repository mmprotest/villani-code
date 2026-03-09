from pathlib import Path

from villani_code.state import Runner


class DummyClient:
    def create_message(self, _payload, stream):
        assert stream is False
        return {"id": "1", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_transcript_includes_durable_events(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    result = runner.run("say done")
    transcript = result["transcript"]
    assert transcript["schema_version"] == "2.0"
    assert transcript["durable_events"]
    assert any(e.get("event_type") == "user_request" for e in transcript["durable_events"])
