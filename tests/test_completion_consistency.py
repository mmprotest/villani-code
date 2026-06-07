from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from villani_code.completion_consistency import (
    HIGH_RISK_WARNING,
    MAX_FINAL_CHECK_MODEL_VISIBLE_CHARS,
    CompletionConsistencyTracker,
    is_masked_validation_command,
)
from villani_code.debug_mode import DebugConfig, DebugMode
from villani_code.execution_context import TaskExecutionContext
from villani_code.state import Runner


def _strong_pass(tracker: CompletionConsistencyTracker, command: str = "check-all") -> None:
    tracker.record_validation(command, 0, "all checks passed", strength=4, clean=True)


def test_required_deliverable_created_but_not_reread_is_high_risk(tmp_path: Path) -> None:
    tracker = CompletionConsistencyTracker("Create report.txt and submit it.", tmp_path)
    tracker.record_write(["report.txt"], created=True)
    _strong_pass(tracker)

    risk = tracker.classify("All requirements are satisfied with direct test evidence.")

    assert risk["level"] == "high"
    assert any("not re-read" in reason for reason in risk["reasons"])


def test_deliverable_reread_removes_unread_signal(tmp_path: Path) -> None:
    tracker = CompletionConsistencyTracker("Create report.txt and submit it.", tmp_path)
    tracker.record_write(["report.txt"], created=True)
    tracker.record_read("report.txt")
    _strong_pass(tracker)

    risk = tracker.classify("report.txt was re-read and its contents directly satisfy the requirement.")

    assert risk["level"] == "low"
    assert not any("not re-read" in reason for reason in risk["high_risk_signals"])


@pytest.mark.parametrize("filter_command", ["head -n 1", "tail -n 1", "grep passed"])
def test_masked_validation_is_weak_evidence(tmp_path: Path, filter_command: str) -> None:
    command = f"check-all | {filter_command}"
    tracker = CompletionConsistencyTracker("Update the implementation.", tmp_path)
    evidence = tracker.record_validation(command, 0, "passed", strength=4)

    risk = tracker.classify("The validation passed.")

    assert is_masked_validation_command(command)
    assert evidence.weakened is True
    assert risk["level"] != "low"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_pipefail_preserves_failing_pipeline_status(tmp_path: Path) -> None:
    context = TaskExecutionContext(tmp_path)
    context.begin_attempt()

    proc, record = context.run("false | cat", tmp_path, 10)
    evidence = context.record_validation(record, kind="project")

    assert proc.returncode != 0
    assert evidence.exit_code != 0


def test_unstable_validation_raises_risk(tmp_path: Path) -> None:
    tracker = CompletionConsistencyTracker("Update the implementation.", tmp_path)
    tracker.record_validation("check-all", 1, "1 failed", strength=4)
    favourable = tracker.record_validation("check-all", 0, "1 passed", strength=4)

    medium = tracker.classify("All requirements have evidence.")
    high = tracker.classify("All requirements have evidence.", final_claim_relies_on_favourable_run=True)

    assert favourable.unstable is True
    assert medium["level"] in {"medium", "high"}
    assert high["level"] == "high"
    assert any("favourable unstable" in reason for reason in high["reasons"])


def test_unresolved_self_identified_defect_is_high_risk(tmp_path: Path) -> None:
    tracker = CompletionConsistencyTracker("Update the implementation.", tmp_path)
    tracker.observe_agent_text("I found a concrete bug in the current behavior.")
    _strong_pass(tracker)

    risk = tracker.classify("Everything else is verified.")

    assert risk["level"] == "high"
    assert any(not issue.resolved for issue in tracker.issues)


def test_self_identified_defect_resolves_after_change_and_validation(tmp_path: Path) -> None:
    tracker = CompletionConsistencyTracker("Update the implementation.", tmp_path)
    tracker.observe_agent_text("I found a concrete bug in the current behavior.")
    tracker.record_write(["module.txt"])
    _strong_pass(tracker)

    assert tracker.issues
    assert all(issue.resolved for issue in tracker.issues)


def test_last_known_better_state_warns_on_regression(tmp_path: Path) -> None:
    tracker = CompletionConsistencyTracker("Update the implementation.", tmp_path)
    _strong_pass(tracker)
    tracker.record_validation("check-all", 1, "1 failed", strength=4)

    assert tracker.current_weaker_than_best() is True


class _HighRiskFinalizationClient:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def create_message(self, payload, stream):
        self.calls += 1
        self.prompts.append(json.dumps(payload.get("messages", [])))
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "write-1", "name": "Write", "input": {"file_path": "report.txt", "content": "value"}}],
            }
        if self.calls in {2, 4}:
            return {"role": "assistant", "content": [{"type": "text", "text": "The task is complete."}]}
        if self.calls in {3, 5}:
            return {"role": "assistant", "content": [{"type": "text", "text": "The required report is weakly supported and was not re-read. No validation was run."}]}
        return {"role": "assistant", "content": [{"type": "text", "text": "Final response."}]}


def test_high_risk_finalization_warns_once_and_writes_artifacts(tmp_path: Path) -> None:
    client = _HighRiskFinalizationClient()
    debug_root = tmp_path / "debug"
    runner = Runner(
        client=client,
        repo=tmp_path,
        model="m",
        stream=False,
        auto_accept_edits=True,
        debug_config=DebugConfig(mode=DebugMode.TRACE, debug_root=debug_root),
    )

    result = runner.run("Create report.txt and submit it.")

    warning_messages = [
        str(block.get("text", ""))
        for message in result["messages"]
        if message.get("role") == "user"
        for block in message.get("content", [])
        if isinstance(block, dict) and HIGH_RISK_WARNING in str(block.get("text", ""))
    ]
    assert len(warning_messages) == 1
    assert result["response"]["content"][0]["text"] == "Final response."
    run_dirs = [path for path in debug_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    artifact_dir = run_dirs[0]
    expected = {
        "final_consistency_check.json",
        "completion_risk.json",
        "required_deliverable_tracking.json",
        "validation_evidence_history.json",
        "unresolved_issues.json",
        "last_known_better_state.json",
    }
    assert expected <= {path.name for path in artifact_dir.iterdir()}
    risk = json.loads((artifact_dir / "completion_risk.json").read_text(encoding="utf-8"))
    checks = json.loads((artifact_dir / "final_consistency_check.json").read_text(encoding="utf-8"))
    assert risk["level"] == "high"
    assert risk["high_risk_warning_count"] == 1
    assert len(checks["checks"]) == 2
    assert all(len(item["model_visible_response"]) <= MAX_FINAL_CHECK_MODEL_VISIBLE_CHARS for item in checks["checks"])
