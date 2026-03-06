from __future__ import annotations

import sys
from pathlib import Path

from villani_code.runtime_safety import (
    ensure_runtime_dependencies_not_shadowed,
    temporary_sys_path,
)
from villani_code.validation_loop import run_validation


def test_repo_with_src_pydantic_does_not_fail_startup_when_not_in_sys_path(
    tmp_path: Path,
) -> None:
    repo = tmp_path
    (repo / "src" / "pydantic").mkdir(parents=True)
    (repo / "src" / "pydantic" / "__init__.py").write_text("ConfigDict = object\n")

    ensure_runtime_dependencies_not_shadowed(repo)


def test_temporary_sys_path_restores_original_path(tmp_path: Path) -> None:
    injected = tmp_path / "repo" / "src"
    injected.mkdir(parents=True)
    original = list(sys.path)

    with temporary_sys_path([injected]):
        assert sys.path[0] == str(injected)

    assert sys.path == original


def test_validation_execution_does_not_persist_sys_path_mutation(tmp_path: Path) -> None:
    (tmp_path / ".villani").mkdir(parents=True)
    (tmp_path / ".villani" / "validation.json").write_text(
        '{"version": 1, "steps": [{"name": "echo", "command": "echo ok", "kind": "inspection", "cost_level": 1, "is_mutating": false, "enabled": true}]}'
    )
    (tmp_path / ".villani" / "repo_map.json").write_text("{}")

    original = list(sys.path)
    result = run_validation(tmp_path, changed_files=[])

    assert result.passed
    assert sys.path == original


def test_runtime_shadowing_detection_flags_dependency_inside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path

    def fake_resolve(name: str) -> Path:
        if name == "pydantic":
            return repo / "src" / "pydantic" / "__init__.py"
        return Path(sys.executable)

    monkeypatch.setattr("villani_code.runtime_safety._resolve_dependency_origin", fake_resolve)

    try:
        ensure_runtime_dependencies_not_shadowed(repo)
    except RuntimeError as exc:
        assert "shadowing" in str(exc)
        assert "pydantic" in str(exc)
    else:
        raise AssertionError("Expected runtime dependency shadowing to be detected")
