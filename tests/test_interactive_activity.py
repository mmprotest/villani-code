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
    model = "test-model"

    def run(self, _text):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


class FakeStatusController:
    def __init__(self):
        self.persistent_lines: list[str] = []
        self.waiting_calls: list[tuple[str, str]] = []
        self.push_actions: list[str] = []
        self.stop_calls: list[tuple[str, str]] = []

    def print_persistent(self, line: str) -> None:
        self.persistent_lines.append(line)

    def start_waiting(self, phase: str, detail: str = "") -> None:
        self.waiting_calls.append((phase, detail))

    def push_action(self, action: str) -> None:
        self.push_actions.append(action)

    def stop_spinner(self, phase: str = "Responding", detail: str = "") -> None:
        self.stop_calls.append((phase, detail))

    def update_phase(self, phase: str, detail: str = "") -> None:
        return None


def test_tool_use_read_emits_tool_and_file_read_activity(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    fake_status = FakeStatusController()
    shell.status_controller = fake_status

    shell._on_runner_event({"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}})

    assert "▶ Using tool: Read — Reading: x.py" in fake_status.persistent_lines
    assert "📖 Read: x.py" in fake_status.persistent_lines


def test_tool_result_success_emits_written_and_patched_lines(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    fake_status = FakeStatusController()
    shell.status_controller = fake_status

    shell._on_runner_event({"type": "tool_use", "tool_use_id": "1", "name": "Write", "input": {"file_path": "a.py"}})
    shell._on_runner_event({"type": "tool_result", "tool_use_id": "1", "is_error": False})
    shell._on_runner_event({
        "type": "tool_use",
        "tool_use_id": "2",
        "name": "Patch",
        "input": {"unified_diff": "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-a\n+b\n"},
    })
    shell._on_runner_event({"type": "tool_result", "tool_use_id": "2", "is_error": False})

    assert "💾 Wrote: a.py" in fake_status.persistent_lines
    assert "🩹 Patched: b.py" in fake_status.persistent_lines


def test_duplicate_activity_is_suppressed_back_to_back(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    fake_status = FakeStatusController()
    shell.status_controller = fake_status

    event = {"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}}
    shell._on_runner_event(event)
    shell._on_runner_event(event)

    assert fake_status.persistent_lines.count("▶ Using tool: Bash — cmd: echo hi") == 1
