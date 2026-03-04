import threading
from pathlib import Path

from villani_code.interactive import InteractiveShell
from villani_code.status_controller import SpinnerTheme


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


class RunnerWithoutPermissions:
    checkpoints = DummyCheckpoints()
    permissions = None

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


def test_approval_prompt_uses_target_for_and_suspends(monkeypatch, tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    fake_status = FakeStatusController()
    shell.status_controller = fake_status

    monkeypatch.setattr(shell, "_approval_choice_dialog", lambda *_args, **_kwargs: "always")

    approved = shell._approval_prompt("Read", {"file_path": "README.md"})

    assert approved is True
    assert fake_status.suspended is True
    assert ("Read", "Read:README.md") in shell._session_approval_allowlist
    assert any(call[0].startswith("Using tool: Read") for call in fake_status.waiting_calls)


def test_approval_prompt_uses_unknown_target_without_permissions(monkeypatch, tmp_path: Path) -> None:
    shell = InteractiveShell(RunnerWithoutPermissions(), tmp_path)
    monkeypatch.setattr(shell, "_approval_choice_dialog", lambda *_args, **_kwargs: "always")

    shell._approval_prompt("Read", {"file_path": "README.md"})

    assert ("Read", "<unknown>") in shell._session_approval_allowlist


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

    toolbar = "".join(part for _style, part in shell._bottom_toolbar())
    assert "[-] Using tool: Read — Reading: src/main.py" in toolbar


def test_resolve_approval_always_adds_allowlist_and_reenables_input(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    event = threading.Event()
    shell._approval_request = {"tool": "Read", "target": "Read:README.md", "choice": None}
    shell._approval_event = event
    shell.input_field.read_only = True

    shell._resolve_approval("always")

    assert ("Read", "Read:README.md") in shell._session_approval_allowlist
    assert shell.input_field.read_only is False
    assert event.is_set()
