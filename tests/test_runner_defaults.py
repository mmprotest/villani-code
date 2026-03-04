from pathlib import Path

from villani_code.permissions import Decision
from villani_code.state import Runner


class DummyClient:
    def create_message(self, _payload, stream):
        raise AssertionError("not used")


def test_runner_default_permissions_ask_for_writes(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)

    write_decision = runner.permissions.evaluate("Write", {"file_path": "a.txt"})
    patch_decision = runner.permissions.evaluate("Patch", {"file_path": "a.txt", "unified_diff": ""})
    read_decision = runner.permissions.evaluate("Read", {"file_path": "a.txt"})

    assert write_decision == Decision.ASK
    assert patch_decision == Decision.ASK
    assert read_decision == Decision.ALLOW
