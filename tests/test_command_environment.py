from __future__ import annotations

import json
import os
from pathlib import Path

from villani_code.command_environment import build_agent_command_environment
from villani_code.debug_mode import DebugConfig, DebugMode
from villani_code.debug_recorder import DebugRecorder
from villani_code.tools import execute_tool


def test_private_path_removed_from_executable_search_path(tmp_path: Path) -> None:
    private_root = tmp_path / "runner-runtime"
    private_entry = private_root / "bin"
    system_entry = tmp_path / "system-tools"

    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment={"PATH": os.pathsep.join((str(private_entry), str(system_entry)))},
        private_roots=(private_root,),
    )

    assert built.values["PATH"] == str(system_entry)
    assert built.diagnostics.path_entries_removed == 1


def test_direct_private_path_variable_removed(tmp_path: Path) -> None:
    private_root = tmp_path / "runner-runtime"
    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment={"INTERNAL_LOCATION": str(private_root / "state")},
        private_roots=(private_root,),
    )

    assert "INTERNAL_LOCATION" not in built.values
    assert built.diagnostics.direct_path_variables_removed == ("INTERNAL_LOCATION",)


def test_non_private_variables_and_path_entries_preserved(tmp_path: Path) -> None:
    private_root = tmp_path / "runner-runtime"
    tool_dir = tmp_path / "workspace-tools"
    source = {"PATH": str(tool_dir), "TASK_SETTING": "enabled", "HOME": str(tmp_path / "home")}

    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment=source,
        private_roots=(private_root,),
    )

    assert built.values == source


def test_building_child_environment_does_not_modify_runner_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_root = tmp_path / "runner-runtime"
    original_path = os.pathsep.join((str(private_root / "bin"), str(tmp_path / "tools")))
    monkeypatch.setenv("PATH", original_path)
    before = dict(os.environ)

    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        private_roots=(private_root,),
    )

    assert built.values["PATH"] != original_path
    assert dict(os.environ) == before


def test_sanitizer_does_not_enumerate_private_root(tmp_path: Path, monkeypatch) -> None:
    private_root = tmp_path / "runner-runtime"
    nested = private_root / "nested" / "deeper"
    nested.mkdir(parents=True)
    (nested / "marker").write_text("unused", encoding="utf-8")

    def fail_iterdir(self: Path):  # pragma: no cover - only runs on regression
        raise AssertionError(f"unexpected directory enumeration: {self}")

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment={"PATH": str(private_root / "bin")},
        private_roots=(private_root,),
    )

    assert built.values["PATH"] == ""


def test_command_environment_diagnostics_are_artifact_only(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private_root = tmp_path / "runner-runtime"
    recorder = DebugRecorder(
        config=DebugConfig(mode=DebugMode.NORMAL, debug_root=tmp_path / "debug"),
        run_id="run-1",
        objective="test command environment",
        repo=workspace,
        mode="execution",
        model="test-model",
    )

    def debug_callback(event_type: str, payload: dict[str, object]) -> None:
        if event_type == "command_environment_sanitized":
            recorder.record_command_environment(
                sanitization_ran=bool(payload["sanitization_ran"]),
                path_entries_removed=int(payload["path_entries_removed"]),
                direct_path_variables_removed=list(payload["direct_path_variables_removed"]),
                variables_flagged=list(payload["variables_flagged"]),
                cwd=str(payload["cwd"]),
                executable=str(payload["executable"]),
                tool_call_id=str(payload["tool_call_id"]),
            )

    source_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", os.pathsep.join((str(private_root / "bin"), source_path)))
    monkeypatch.setenv("PRIVATE_STATE", str(private_root / "state"))
    result = execute_tool(
        "Bash",
        {"command": "echo ok"},
        workspace,
        debug_callback=debug_callback,
        tool_call_id="tool-1",
        private_roots=(private_root,),
    )

    rows = [
        json.loads(line)
        for line in recorder.artifacts.path("commands.jsonl").read_text().splitlines()
    ]
    diagnostic = next(row for row in rows if row.get("event") == "command_environment_sanitized")
    assert diagnostic["sanitization_ran"] is True
    assert diagnostic["path_entries_removed"] == 1
    assert diagnostic["direct_path_variables_removed"] == ["PRIVATE_STATE"]
    assert diagnostic["cwd"] == str(workspace)
    assert "sanitization_ran" not in result["content"]
    assert "path_entries_removed" not in result["content"]
