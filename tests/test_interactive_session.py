from pathlib import Path

from villani_code.interactive import SessionStore


def test_session_store_save_and_load(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, model="m", base_url="http://localhost", session_id="abc")
    messages = [{"role": "user", "content": "hi"}]
    store.save(messages)

    loaded = store.load()
    assert loaded["session_id"] == "abc"
    assert loaded["messages"] == messages
    assert loaded["repo_path"] == str(tmp_path)
