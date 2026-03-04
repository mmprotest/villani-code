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


def test_banner_text_includes_model_line(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    banner = "".join(text for _style, text in shell._banner_text())

    assert "villani-fying your terminal" in banner
    assert "Model:" in banner


def test_append_log_uses_incremental_buffer_update(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)

    shell._append_log("first")
    first_text = shell.log_area.buffer.text
    shell._append_log("second")

    assert first_text == "first"
    assert shell.log_area.buffer.text.endswith("\nsecond")
    assert "\n".join(shell.log_lines) == shell.log_area.buffer.text
