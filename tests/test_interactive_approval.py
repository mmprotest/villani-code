import threading
import time

from villani_code.interactive import ApprovalManager


def test_approval_manager_bridges_worker_and_ui_threads() -> None:
    events = []
    manager = ApprovalManager(events.append)
    result = {}

    def worker() -> None:
        result["decision"] = manager.request_approval("Bash", {"command": "echo hi"}, "tool-1")

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(20):
        if events:
            break
        time.sleep(0.01)
    assert events
    req_id = events[0]["id"]
    manager.resolve(req_id, "allow_once")
    thread.join(timeout=1)
    assert result["decision"] == "allow_once"


def test_approval_manager_escape_resolves_deny_without_deadlock() -> None:
    events = []
    manager = ApprovalManager(events.append)

    finished = threading.Event()

    def worker() -> None:
        manager.request_approval("Bash", {"command": "echo hi"}, "tool-2")
        finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(20):
        if events:
            break
        time.sleep(0.01)
    manager.resolve(events[0]["id"], "deny")
    assert finished.wait(timeout=1)
    thread.join(timeout=1)


def test_approval_manager_background_decision() -> None:
    events = []
    manager = ApprovalManager(events.append)
    result = {}

    def worker() -> None:
        result["decision"] = manager.request_approval("Bash", {"command": "echo hi"}, "tool-3")

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(20):
        if events:
            break
        time.sleep(0.01)
    manager.resolve(events[0]["id"], "background_allow")
    thread.join(timeout=1)
    assert result["decision"] == "background_allow"
