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


def _runner(tmp_path: Path, *, villani_mode: bool = True) -> Runner:
    runner = Runner(client=_Client(), repo=tmp_path, model="m", stream=False, small_model=True, plan_mode="off", villani_mode=villani_mode)
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


def _sandbox_policy(phase: str = "scaffold_project") -> dict[str, object]:
    policy = _greenfield_policy(phase)
    policy["unrestricted_within_sandbox"] = True
    policy["allow_mutating_tools"] = True
    policy["allow_shell_commands"] = True
    policy["allow_validation_shell"] = True
    policy["max_new_files_per_node"] = 0
    policy["max_distinct_paths_per_node"] = 0
    policy["max_total_write_bytes_per_node"] = 0
    return policy


_ALL_STAGES = [
    "inspect_workspace",
    "scaffold_project",
    "implement_increment",
    "validate_project",
    "summarize_outcome",
]


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
    runner._villani_phase_tool_policy = _sandbox_policy("scaffold_project")

    blocked = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "../outside.txt", "content": "nope\n"},
        "x1",
        1,
    )
    assert blocked["is_error"] is True
    assert "sandbox_boundary_blocked" in str(blocked["content"])


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


def test_greenfield_sandbox_shell_is_unrestricted_inside_root(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _sandbox_policy("implement_increment")

    result = execute_tool_with_policy(
        runner,
        "Bash",
        {"command": "mkdir -p build && python -c \"print('ok')\" > build/out.txt", "cwd": ".", "timeout_sec": 5},
        "b2",
        1,
    )
    assert result["is_error"] is False
    assert (tmp_path / "build" / "out.txt").exists()


def test_greenfield_new_file_whole_write_allowed(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _sandbox_policy("scaffold_project")
    medium_content = "x = 1\n" * 1500

    err = runner._small_model_tool_guard("Write", {"file_path": "src/generated.py", "content": medium_content})
    assert err is None


def test_greenfield_sandbox_full_project_creation_allowed(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _sandbox_policy("scaffold_project")
    writes = [
        ("README.md", "# demo\n"),
        ("pyproject.toml", "[project]\nname='demo'\nversion='0.1.0'\n"),
        (".gitignore", "__pycache__/\n.venv/\n"),
        ("src/demo/__init__.py", "__all__=['main']\n"),
        ("src/demo/main.py", "def main():\n    return 'ok'\n"),
        ("tests/test_main.py", "from demo.main import main\n\ndef test_main():\n    assert main() == 'ok'\n"),
        ("scripts/dev.sh", "#!/usr/bin/env bash\npython -m pytest -q\n"),
    ]
    for idx, (path, content) in enumerate(writes, start=1):
        result = execute_tool_with_policy(runner, "Write", {"file_path": path, "content": content}, f"p{idx}", idx)
        assert result["is_error"] is False


def test_greenfield_sandbox_mutations_patch_delete_rename_allowed(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _sandbox_policy("implement_increment")
    seed = execute_tool_with_policy(runner, "Write", {"file_path": "src/app.py", "content": "x = 1\n"}, "m1", 1)
    assert seed["is_error"] is False
    patched = execute_tool_with_policy(
        runner,
        "Patch",
        {"file_path": "src/app.py", "unified_diff": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"},
        "m2",
        2,
    )
    assert patched["is_error"] is False
    moved = execute_tool_with_policy(
        runner,
        "Bash",
        {"command": "mv src/app.py src/renamed.py && rm -f src/renamed.py", "cwd": ".", "timeout_sec": 5},
        "m3",
        3,
    )
    assert moved["is_error"] is False
    assert not (tmp_path / "src" / "app.py").exists()
    assert not (tmp_path / "src" / "renamed.py").exists()


def test_greenfield_sandbox_shell_outside_reference_blocked(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _sandbox_policy("implement_increment")
    blocked = execute_tool_with_policy(
        runner,
        "Bash",
        {"command": "cat ../oops.txt", "cwd": ".", "timeout_sec": 5},
        "s1",
        1,
    )
    assert blocked["is_error"] is True
    assert "sandbox_boundary_blocked" in str(blocked["content"])


def test_greenfield_sandbox_internal_errors_do_not_use_legacy_policy_words(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _sandbox_policy("scaffold_project")
    blocked = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "../outside.txt", "content": "nope\n"},
        "e1",
        1,
    )
    assert blocked["is_error"] is True
    text = str(blocked["content"])
    assert "authoritative" not in text
    assert "scope expansion" not in text
    assert "new-file budget" not in text
    assert "greenfield_shell_blocked" not in text


def test_repo_local_bounded_scope_lock_behavior_unchanged(tmp_path: Path) -> None:
    runner = _runner(tmp_path, villani_mode=False)
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


def test_villani_unrestricted_shell_allowed_in_all_stages(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "some_file.py").write_text("x=1\n", encoding="utf-8")
    commands = [
        "mkdir -p tmp && python -V",
        "python -m pytest --help",
        "python -m py_compile some_file.py",
    ]
    for idx, phase in enumerate(_ALL_STAGES, start=1):
        runner._villani_phase_tool_policy = _sandbox_policy(phase)
        for cmd in commands:
            result = execute_tool_with_policy(runner, "Bash", {"command": cmd, "cwd": ".", "timeout_sec": 5}, f"s{idx}", idx)
            assert result["is_error"] is False


def test_villani_unrestricted_mutations_allowed_in_all_stages(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    files = [f"generated/file_{i}.txt" for i in range(12)]
    core = [
        ".gitignore",
        "README.md",
        "pyproject.toml",
        "requirements.txt",
    ]
    for idx, phase in enumerate(_ALL_STAGES, start=1):
        runner._villani_phase_tool_policy = _sandbox_policy(phase)
        for rel in files + core:
            result = execute_tool_with_policy(runner, "Write", {"file_path": rel, "content": f"{phase}:{rel}\n"}, f"w{idx}", idx)
            assert result["is_error"] is False
        patch = execute_tool_with_policy(
            runner,
            "Patch",
            {"file_path": "README.md", "unified_diff": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-" + f"{phase}:README.md\n+" + f"{phase}:patched\n"},
            f"p{idx}",
            idx,
        )
        assert patch["is_error"] is False
        moved = execute_tool_with_policy(
            runner,
            "Bash",
            {"command": "mv requirements.txt requirements-dev.txt && rm -f requirements-dev.txt", "cwd": ".", "timeout_sec": 5},
            f"m{idx}",
            idx,
        )
        assert moved["is_error"] is False


def test_villani_unrestricted_internal_denials_do_not_emit_legacy_policy_errors(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner._villani_phase_tool_policy = _sandbox_policy("summarize_outcome")
    result = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "inside.txt", "content": "ok\n"},
        "legacy1",
        1,
    )
    assert result["is_error"] is False
    transcript = str(result["content"]).lower()
    for blocked in [
        "greenfield_shell_blocked",
        "target path is not authoritative",
        "new-file budget",
        "constrained scope lock",
        "summarize_outcome shell blocked",
        "small-model mode policy",
    ]:
        assert blocked not in transcript
