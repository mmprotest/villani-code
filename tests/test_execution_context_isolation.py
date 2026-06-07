from __future__ import annotations

import json
import os
from pathlib import Path

from villani_code.debug_mode import DebugConfig, DebugMode
from villani_code.debug_recorder import DebugRecorder
from villani_code.execution import ExecutionBudget
from villani_code.execution_context import PRIVATE_WARNING, TaskExecutionContext
from villani_code.state import Runner
from villani_code.tools import execute_tool


class CaptureClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def create_message(self, payload, stream=False):
        self.payloads.append(payload)
        return {
            "id": "done",
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "finished"}],
        }


def _make_executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_task_command_environment_strips_private_runtime_from_path(
    tmp_path: Path, monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    private = tmp_path / "runner-private"
    workspace.mkdir()
    private.mkdir()
    _make_executable(private / "private-command")
    monkeypatch.setenv("PATH", os.pathsep.join([str(private), os.environ.get("PATH", "")]))
    monkeypatch.setenv("VIRTUAL_ENV", str(private))

    context = TaskExecutionContext(workspace, private_paths=[private])
    proc, record = context.run("printf '%s' \"$PATH\"", workspace, 10)

    assert proc.returncode == 0
    assert str(private) not in proc.stdout.split(os.pathsep)
    assert str(private) not in record.before.path.split(os.pathsep)
    assert "VIRTUAL_ENV" not in record.before.environment_names


def test_private_runtime_mutation_warns_in_tool_context_and_debug_artifact(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    private = tmp_path / "runner-private"
    debug_root = tmp_path / "debug"
    workspace.mkdir()
    private.mkdir()
    context = TaskExecutionContext(workspace, private_paths=[private])

    result = execute_tool(
        "Bash",
        {"command": f"printf changed > {private / 'state'}", "cwd": ".", "timeout_sec": 10},
        workspace,
        unsafe=True,
        execution_context=context,
    )
    payload = json.loads(result["content"])
    context.finish_attempt()

    assert PRIVATE_WARNING in payload["warnings"]
    assert PRIVATE_WARNING in context.attempt.warnings
    assert "private-runtime" in payload["execution_context"]["path_classes"]
    assert payload["validation_evidence"]["label"] == "smoke test in polluted/private context"

    recorder = DebugRecorder(
        DebugConfig(mode=DebugMode.TRACE, debug_root=debug_root),
        run_id="isolation",
        objective="generic task",
        repo=workspace,
        mode="execution",
        model="test-model",
    )
    recorder.write_attempt_state(context.attempt.to_dict())
    artifact = json.loads((debug_root / "isolation" / "attempt_state.json").read_text())
    assert PRIVATE_WARNING in artifact["attempt_state"]["warnings"]


def test_cumulative_attempt_state_keeps_all_files_when_last_is_helper(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = TaskExecutionContext(workspace)
    context.begin_attempt()

    (workspace / "primary-one").write_text("one", encoding="utf-8")
    (workspace / "primary-two").write_text("two", encoding="utf-8")
    (workspace / "helper-last").write_text("temporary", encoding="utf-8")
    attempt = context.finish_attempt()

    created_names = {Path(path).name for path in attempt.files_created}
    assert created_names == {"primary-one", "primary-two", "helper-last"}
    assert attempt.side_effects[-1]["scope"] == "cumulative-attempt"


def test_clean_failure_outweighs_polluted_success_and_records_contradiction(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    private = tmp_path / "runner-private"
    workspace.mkdir()
    private.mkdir()
    private_check = private / "private-check"
    _make_executable(private_check)
    context = TaskExecutionContext(workspace, private_paths=[private])

    _proc, polluted = context.run(str(private_check), workspace, 10)
    polluted_evidence = context.record_validation(polluted, kind="smoke")
    _proc, clean = context.run("exit 1", workspace, 10)
    clean_evidence = context.record_validation(clean, kind="smoke", final_behavior=True)

    assert polluted_evidence.label == "smoke test in polluted/private context"
    assert clean_evidence.label == "independent smoke test in clean task context"
    assert clean_evidence.strength > polluted_evidence.strength
    assert polluted_evidence.suspicious is True
    assert context.attempt.unresolved_failures


def test_retry_receives_structured_failure_memory_with_context_difference(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    private = tmp_path / "runner-private"
    workspace.mkdir()
    private.mkdir()
    private_check = private / "private-check"
    _make_executable(private_check)
    client = CaptureClient()
    runner = Runner(
        client=client,
        repo=workspace,
        model="test-model",
        stream=False,
        private_runtime_paths=[private],
    )

    _proc, polluted = runner._task_execution_context.run(str(private_check), workspace, 10)
    runner._task_execution_context.record_validation(polluted, kind="smoke")
    memory = runner.record_final_validation(
        succeeded=False,
        summary="final clean-context validation failed",
        believed_succeeded="the private-context smoke check passed",
    )
    assert memory is not None

    runner.run(
        "retry the task",
        execution_budget=ExecutionBudget(
            max_turns=2,
            max_tool_calls=2,
            max_seconds=10,
            max_no_edit_turns=2,
            max_reconsecutive_recon_turns=2,
        ),
    )

    rendered_messages = json.dumps(client.payloads[0]["messages"])
    assert "Previous attempt failure memory" in rendered_messages
    assert "Something passed in one context but failed in another" in rendered_messages
    assert "private-runtime dependency" in rendered_messages
