from pathlib import Path

from villani_code.interactive import InteractiveShell


class DummyCheckpoints:
    def create(self, *_args, **_kwargs):
        return None

    def list(self):
        return []


class DummyPermissions:
    def target_for(self, tool: str, payload: dict) -> str:
        return f"{tool}:{payload.get('file_path', '<none>')}"


class DummyRunner:
    checkpoints = DummyCheckpoints()
    permissions = DummyPermissions()

    def run(self, _text):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


def test_payload_preview_truncation_and_hints(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    payload = {
        "path": "src/main.py",
        "diff": "\n".join(f"line {i}" for i in range(100)),
        "content": "x" * 300,
        "blob": "y" * 400,
    }

    preview = shell._format_approval_preview("Patch", "src/main.py", payload)

    assert "tool: Patch" in preview
    assert "target: src/main.py" in preview
    assert "diff: 100 lines (use /diff to inspect)" in preview
    assert "content: 300 chars" in preview
    assert "... (truncated)" in preview
    assert max(len(line) for line in preview.splitlines()) <= 160


def test_append_log_uses_incremental_buffer_update(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)

    shell._append_log("first")
    first_text = shell.log_area.buffer.text
    shell._append_log("second")

    assert first_text == "first"
    assert shell.log_area.buffer.text.endswith("\nsecond")
    assert "\n".join(shell.log_lines) == shell.log_area.buffer.text
