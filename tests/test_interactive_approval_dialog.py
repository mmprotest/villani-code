from pathlib import Path

from villani_code.interactive import InteractiveShell
from villani_code.status_controller import SpinnerTheme


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


class FakePromptSession:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    def prompt(self):
        self.calls += 1
        return self.response


def test_approval_prompt_uses_dialog_and_suspends(monkeypatch, tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    fake_status = FakeStatusController()
    shell.status_controller = fake_status

    monkeypatch.setattr("villani_code.interactive.PermissionEngine._target_for", lambda *_args, **_kwargs: "repo/**")

    monkeypatch.setattr(shell, "_approval_choice_dialog", lambda *_args, **_kwargs: "always")

    approved = shell._approval_prompt("Read", {"file_path": "README.md"})

    assert approved is True
    assert fake_status.suspended is True
    assert ("Read", "repo/**") in shell._session_approval_allowlist
    assert any(call[0].startswith("Using tool: Read") for call in fake_status.waiting_calls)


def test_bottom_toolbar_includes_spinner_frame_and_detail(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    theme = SpinnerTheme(["-", "\\"], ["slogan"], ["micro"])
    with shell.status_controller._lock:
        shell.status_controller._themes = [theme]
        shell.status_controller._theme = theme
        shell.status_controller.current_phase = "Using tool: Read"
        shell.status_controller.current_detail = "Reading: src/main.py"
        shell.status_controller._spinning = True
        shell.status_controller._frame_index = 0

    assert "[-] Using tool: Read — Reading: src/main.py" in shell._bottom_toolbar()


def test_prompt_for_input_suspends_status_before_prompt(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    fake_status = FakeStatusController()
    shell.status_controller = fake_status
    fake_session = FakePromptSession("hello")

    text = shell._prompt_for_input(fake_session)

    assert text == "hello"
    assert fake_session.calls == 1
    assert fake_status.suspended is True
