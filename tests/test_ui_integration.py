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


def test_keybinding_registration_and_actions(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    kb = shell._build_keybindings()
    bindings = {b.keys for b in kb.bindings}
    assert ("c-p",) in bindings
    assert ("c-s",) in bindings
    assert ("c-d",) in bindings
    assert ("c-f",) in bindings
    assert ("c-_",) in bindings
