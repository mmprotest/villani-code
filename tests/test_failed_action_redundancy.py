from __future__ import annotations

import json
from pathlib import Path

from villani_code.context_projection import build_model_context_packet, render_model_context_packet
from villani_code.state import Runner


class DummyClient:
    def create_message(self, _payload, stream=False):
        return {"content": [{"type": "text", "text": "ok"}]}


def _seed_repo(repo: Path) -> None:
    (repo / "villani_code").mkdir(parents=True, exist_ok=True)
    (repo / "villani_code" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


def test_repeated_failed_shell_command_detected_as_redundant(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("objective")

    runner._record_failed_action("Bash", {"command": "pytest   -q", "cwd": "."}, "Traceback: boom")
    assert runner._redundant_failed_action_detected is False
    runner._record_failed_action("Bash", {"command": "pytest -q", "cwd": "."}, "Traceback: boom")

    assert runner._redundant_failed_action_detected is True
    assert "shell_command" in runner._redundant_failed_action_summary


def test_repeated_failed_write_and_patch_detected_as_redundant(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("objective")
    (tmp_path / "villani_code" / "app.py").write_text("x = 1\n", encoding="utf-8")

    runner._record_failed_action(
        "Write",
        {"file_path": "villani_code/app.py", "content": "x = 2\n"},
        "write denied",
    )
    runner._record_failed_action(
        "Write",
        {"file_path": "./villani_code/app.py", "content": "x = 2\n"},
        "write denied",
    )
    assert runner._redundant_failed_action_detected is True

    diff = "--- a/villani_code/app.py\n+++ b/villani_code/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    runner._record_failed_action("Patch", {"unified_diff": diff}, "patch failed: context mismatch")
    runner._record_failed_action("Patch", {"unified_diff": diff}, "patch failed: context mismatch")
    assert runner._redundant_failed_action_detected is True


def test_changed_evidence_resets_redundancy_detection(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("objective")
    target = tmp_path / "villani_code" / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")

    tool_input = {"file_path": "villani_code/app.py", "content": "x = 2\n"}
    runner._record_failed_action("Write", tool_input, "write denied")
    runner._record_failed_action("Write", tool_input, "write denied")
    assert runner._redundant_failed_action_detected is True

    target.write_text("x = 42\n", encoding="utf-8")
    runner._record_failed_action("Write", tool_input, "write denied")
    assert runner._redundant_failed_action_detected is False


def test_context_projection_includes_compact_redundancy_state(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("objective")
    runner._record_failed_action("Bash", {"command": "pytest -q"}, "Traceback: boom")
    runner._record_failed_action("Bash", {"command": "pytest -q"}, "Traceback: boom")

    packet = build_model_context_packet(runner)
    rendered = render_model_context_packet(packet)

    assert "Redundant failed action detected: true" in rendered
    assert "same failed" in rendered
    assert "admonition" not in rendered.lower()


def test_raw_runtime_events_preserve_repeated_failures(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("objective")

    runner.event_callback({"type": "tool_result", "name": "Bash", "is_error": True, "content": "boom"})
    runner.event_callback({"type": "tool_result", "name": "Bash", "is_error": True, "content": "boom"})
    events_path = runner._mission_dir / "runtime_events.jsonl"
    rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tool_rows = [row for row in rows if row.get("type") == "tool_result"]
    assert len(tool_rows) == 2


def test_regression_noisy_run_redundant_then_materially_changed_retry(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("objective")

    runner._record_failed_action("Bash", {"command": "pytest -q"}, "Traceback: boom")
    assert runner._redundant_failed_action_detected is False

    runner._record_failed_action("Bash", {"command": "pytest -q"}, "Traceback: boom")
    assert runner._redundant_failed_action_detected is True

    runner._record_failed_action("Bash", {"command": "pytest -q tests/test_state_runtime.py"}, "Traceback: boom")
    assert runner._redundant_failed_action_detected is False

    assert runner._mission_state is not None
    assert len(runner._mission_state.recent_failed_action_fingerprints) >= 3


def test_operation_based_fingerprint_not_task_keyword_based(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    runner._ensure_mission("objective")

    runner._record_failed_action("Bash", {"command": "run_bugfix_task --mode fast"}, "boom")
    payload = json.loads(runner._last_failed_action_fingerprint)
    assert payload["operation"] == "shell_command"
    assert "task" not in payload
