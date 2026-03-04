from pathlib import Path

from villani_code.interactive import InteractiveShell


class DummyCheckpoints:
    def create(self, *_args, **_kwargs):
        return None

    def list(self):
        return []


class DummyRunner:
    checkpoints = DummyCheckpoints()

    def run(self, _text):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


def test_extract_total_tokens_prefers_input_output(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)

    tokens = shell._extract_total_tokens({"usage": {"input_tokens": 11, "output_tokens": 7}}, "hello world")

    assert tokens == 18


def test_extract_total_tokens_falls_back_to_total_then_heuristic(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)

    assert shell._extract_total_tokens({"usage": {"total_tokens": 23}}, "hello world") == 23
    assert shell._extract_total_tokens({}, "hello world") == 22
