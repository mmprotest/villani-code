from pathlib import Path

from villani_code.interactive import InteractiveShell


class DummyCheckpoints:
    def create(self, *_args, **_kwargs):
        return None

    def list(self):
        return []


class DummyRunner:
    checkpoints = DummyCheckpoints()
    permissions = object()

    def run(self, _text):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


class FakeStatusController:
    def __init__(self):
        self.suspended = False
        self.waiting_calls = []
        self.updated_calls = []

    def suspend(self):
        self.suspended = True

    def start_waiting(self, phase, detail=""):
        self.waiting_calls.append((phase, detail))

    def update_phase(self, phase, detail=""):
        self.updated_calls.append((phase, detail))


def test_approval_prompt_uses_dialog_and_suspends(monkeypatch, tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    fake_status = FakeStatusController()
    shell.status_controller = fake_status

    monkeypatch.setattr("villani_code.interactive.PermissionEngine._target_for", lambda *_args, **_kwargs: "repo/**")

    class _Dialog:
        def run(self):
            return "always"

    monkeypatch.setattr("villani_code.interactive.radiolist_dialog", lambda **_kwargs: _Dialog())

    approved = shell._approval_prompt("Read", {"file_path": "README.md"})

    assert approved is True
    assert fake_status.suspended is True
    assert ("Read", "repo/**") in shell._session_approval_allowlist
    assert any(call[0].startswith("Using tool: Read") for call in fake_status.waiting_calls)
