import threading
import time
from pathlib import Path

from villani_code.tui.controller import RunnerController


class DummyRunner:
    permissions = None


class DummyApp:
    def __init__(self):
        self.messages = []

    def post_message(self, message):
        self.messages.append(message)


def test_approval_bridge_blocks_worker_until_resolved(tmp_path: Path) -> None:
    controller = RunnerController(DummyRunner(), DummyApp())
    result = {}

    def worker() -> None:
        result["approved"] = controller.request_approval("Read", {"file_path": "README.md"})

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)
    req = next(m for m in controller.app.messages if m.__class__.__name__ == "ApprovalRequest")
    controller.resolve_approval(req.request_id, "yes")
    t.join(timeout=1)

    assert result["approved"] is True
