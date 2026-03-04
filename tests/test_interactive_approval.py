import threading
import time

from villani_code.interactive import ApprovalManager


def test_approval_manager_bridges_worker_and_ui_threads() -> None:
    events = []
    manager = ApprovalManager(events.append)
    result = {}

    def worker() -> None:
        result["ok"] = manager.request_approval("Bash", {"command": "echo hi"})

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(20):
        if events:
            break
        time.sleep(0.01)
    assert events
    req_id = events[0]["id"]
    manager.resolve(req_id, True)
    thread.join(timeout=1)
    assert result["ok"] is True
