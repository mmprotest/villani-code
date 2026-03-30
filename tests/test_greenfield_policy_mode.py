from __future__ import annotations

from pathlib import Path

from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy


class _Client:
    def create_message(self, _payload, stream):
        assert stream is False
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


class _Hooks:
    def run_event(self, *_args, **_kwargs):
        return type("Hook", (), {"allow": True, "reason": ""})()


class _PermissivePermissions:
    def evaluate_with_reason(self, *_args, **_kwargs):
        from villani_code.permissions import Decision

        return type("P", (), {"decision": Decision.ALLOW, "reason": ""})()


def _runner(tmp_path: Path) -> Runner:
    runner = Runner(client=_Client(), repo=tmp_path, model="m", stream=False, small_model=True)
    runner.hooks = _Hooks()
    runner.permissions = _PermissivePermissions()
    runner.set_active_tool_root(tmp_path)
    return runner


def _greenfield_policy(phase: str = "scaffold_project") -> dict[str, object]:
    return {
        "mission_type": "greenfield_build",
        "node_phase": phase,
        "node_id": "n1",
        "allow_mutating_tools": phase in {"scaffold_project", "implement_increment"},
        "allow_shell_commands": phase in {"scaffold_project", "implement_increment", "validate_project"},
        "allow_validation_shell": phase in {"scaffold_project", "implement_increment", "validate_project"},
        "max_new_files_per_node": 8,
        "max_distinct_paths_per_node": 10,
        "max_total_write_bytes_per_node": 120_000,
        "new_file_whole_write_max_bytes": 100_000,
    }


def test_greenfield_scaffold_can_widen_within_workspace(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _greenfield_policy("scaffold_project")

    writes = [
        ("src/game.py", "def run():\n    return 'ok'\n"),
        ("src/main.py", "from src.game import run\nprint(run())\n"),
        ("tests/test_game.py", "def test_smoke():\n    assert True\n"),
        ("pyproject.toml", "[project]\nname='demo'\nversion='0.1.0'\n"),
    ]
    for idx, (path, content) in enumerate(writes, start=1):
        result = execute_tool_with_policy(runner, "Write", {"file_path": path, "content": content}, str(idx), idx)
        assert result["is_error"] is False
        assert "Constrained scope lock" not in str(result["content"])


def test_greenfield_write_cannot_escape_workspace_root(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _greenfield_policy("scaffold_project")

    blocked = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "../outside.txt", "content": "nope\n"},
        "x1",
        1,
    )
    assert blocked["is_error"] is True
    assert "workspace boundary" in str(blocked["content"])


def test_greenfield_safe_shell_validation_allowed(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _greenfield_policy("scaffold_project")

    result = execute_tool_with_policy(
        runner,
        "Bash",
        {"command": "python -m compileall .", "cwd": ".", "timeout_sec": 5},
        "b1",
        1,
    )
    assert result["is_error"] is False


def test_greenfield_dangerous_shell_still_blocked(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _greenfield_policy("implement_increment")

    blocked = execute_tool_with_policy(
        runner,
        "Bash",
        {"command": "rm -rf .", "cwd": ".", "timeout_sec": 5},
        "b2",
        1,
    )
    assert blocked["is_error"] is True
    assert "allowlist rejected" in str(blocked["content"])


def test_greenfield_new_file_whole_write_allowed(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _greenfield_policy("scaffold_project")
    medium_content = "x = 1\n" * 1500

    err = runner._small_model_tool_guard("Write", {"file_path": "src/generated.py", "content": medium_content})
    assert err is None


def test_repo_local_bounded_scope_lock_behavior_unchanged(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "readme.md").write_text("# docs\n", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
    runner._intended_targets = {"src/app.py"}

    first = runner._small_model_tool_guard("Patch", {"file_path": "docs/readme.md", "patch": "x"})
    assert first is None
    err = runner._small_model_tool_guard("Patch", {"file_path": "docs/guide.md", "patch": "x"})
    assert isinstance(err, str)
    assert "Constrained scope lock" in err
