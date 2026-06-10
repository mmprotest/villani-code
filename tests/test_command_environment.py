from __future__ import annotations

import json
import os
from pathlib import Path

from villani_code.command_environment import (
    build_agent_command_environment,
    runner_private_roots,
)
from villani_code.debug_mode import DebugConfig, DebugMode
from villani_code.debug_recorder import DebugRecorder
from villani_code.tools import execute_tool


def test_discovers_private_root_from_runner_owned_directory_variable(tmp_path: Path) -> None:
    roots = runner_private_roots(
        workspace=tmp_path / "workspace",
        source_environment={"RUNNER_RUNTIME_ROOT": "/private/runtime"},
    )

    assert Path("/private/runtime") in roots


def test_discovers_private_root_from_runner_owned_executable_variable(tmp_path: Path) -> None:
    roots = runner_private_roots(
        workspace=tmp_path / "workspace",
        source_environment={"VILLANI_EXECUTABLE": "/private/runtime/bin/tool"},
    )

    assert Path("/private/runtime") in roots


def test_private_path_discovered_from_environment_is_removed(tmp_path: Path) -> None:
    system_entry = "/usr/bin"
    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment={
            "RUNNER_RUNTIME_ROOT": "/private/runtime",
            "PATH": os.pathsep.join(("/private/runtime/bin", system_entry)),
        },
    )

    assert built.values["PATH"] == system_entry
    assert built.diagnostics.path_entries_removed == 1


def test_direct_private_path_variable_discovered_from_environment_is_removed(
    tmp_path: Path,
) -> None:
    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment={
            "RUNNER_RUNTIME_ROOT": "/private/runtime",
            "RUNNER_STATE": "/private/runtime/state",
            "INTERNAL_LOCATION": "/private/runtime/cache",
        },
    )

    assert "RUNNER_RUNTIME_ROOT" not in built.values
    assert "RUNNER_STATE" not in built.values
    assert "INTERNAL_LOCATION" not in built.values


def test_non_private_task_variable_is_preserved(tmp_path: Path) -> None:
    source = {
        "RUNNER_RUNTIME_ROOT": "/private/runtime",
        "TASK_LOCATION": str(tmp_path / "workspace" / "task-data"),
    }

    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment=source,
    )

    assert built.values["TASK_LOCATION"] == source["TASK_LOCATION"]


def test_building_child_environment_does_not_modify_runner_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_path = os.pathsep.join(("/private/runtime/bin", "/usr/bin"))
    monkeypatch.setenv("RUNNER_RUNTIME_ROOT", "/private/runtime")
    monkeypatch.setenv("PATH", original_path)
    before = dict(os.environ)

    built = build_agent_command_environment(workspace=tmp_path / "workspace")

    assert built.values["PATH"] == "/usr/bin"
    assert dict(os.environ) == before


def test_discovery_and_sanitizer_do_not_enumerate_private_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_root = tmp_path / "runner-runtime"
    nested = private_root / "nested" / "deeper"
    nested.mkdir(parents=True)
    for index in range(100):
        (nested / f"marker-{index}").write_text("unused", encoding="utf-8")

    def fail_iterdir(self: Path):  # pragma: no cover - only runs on regression
        raise AssertionError(f"unexpected directory enumeration: {self}")

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment={
            "RUNNER_RUNTIME_ROOT": str(private_root),
            "PATH": str(private_root / "bin"),
        },
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
                discovered_private_roots=list(payload["discovered_private_roots"]),
                environment_variables_removed=list(payload["environment_variables_removed"]),
                path_entries_removed=int(payload["path_entries_removed"]),
                runner_owned_variables_considered=list(
                    payload["runner_owned_variables_considered"]
                ),
                possible_private_path_variables_flagged=list(
                    payload["possible_private_path_variables_flagged"]
                ),
                cwd=str(payload["cwd"]),
                executable=str(payload["executable"]),
                tool_call_id=str(payload["tool_call_id"]),
            )

    source_path = os.environ.get("PATH", "")
    monkeypatch.setenv("RUNNER_RUNTIME_ROOT", str(private_root))
    monkeypatch.setenv("PATH", os.pathsep.join((str(private_root / "bin"), source_path)))
    monkeypatch.setenv("PRIVATE_STATE", str(private_root / "state"))
    result = execute_tool(
        "Bash",
        {"command": "echo ok"},
        workspace,
        debug_callback=debug_callback,
        tool_call_id="tool-1",
    )

    rows = [
        json.loads(line)
        for line in recorder.artifacts.path("commands.jsonl").read_text().splitlines()
    ]
    diagnostic = next(row for row in rows if row.get("event") == "command_environment_sanitized")
    assert str(private_root.resolve()) in diagnostic["discovered_private_roots"]
    assert {"PRIVATE_STATE", "RUNNER_RUNTIME_ROOT"}.issubset(
        diagnostic["environment_variables_removed"]
    )
    assert diagnostic["path_entries_removed"] == 1
    assert diagnostic["runner_owned_variables_considered"] == ["RUNNER_RUNTIME_ROOT"]
    assert diagnostic["possible_private_path_variables_flagged"] == []
    assert diagnostic["cwd"] == str(workspace)
    assert "discovered_private_roots" not in result["content"]
    assert "environment_variables_removed" not in result["content"]
    assert "path_entries_removed" not in result["content"]


def test_runner_runtime_path_is_removed_while_system_toolchains_remain(tmp_path: Path) -> None:
    system_entries = ("/usr/local/bin", "/usr/bin")
    built = build_agent_command_environment(
        workspace=tmp_path / "workspace",
        source_environment={
            "VILLANI_RUNTIME_ROOT": "/private/runtime",
            "PATH": os.pathsep.join(("/private/runtime/bin", *system_entries)),
        },
    )

    child_entries = built.values["PATH"].split(os.pathsep)
    assert "/private/runtime/bin" not in child_entries
    assert child_entries == list(system_entries)
