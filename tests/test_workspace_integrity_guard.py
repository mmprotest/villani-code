from __future__ import annotations

import subprocess
from pathlib import Path

from villani_code.workspace_integrity import WorkspaceIntegrityGuard


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".validator_cache/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


def test_shell_scratch_file_removed_before_finalize(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    guard = WorkspaceIntegrityGuard(repo)
    (repo / "scratch.txt").write_text("tmp", encoding="utf-8")
    guard.record_write(path="scratch.txt", operation="create", actor="shell command", intentionally_created=False, reason="scratch")
    result = guard.check_and_cleanup()
    assert not (repo / "scratch.txt").exists()
    assert "scratch.txt" in result.removed_paths


def test_validator_cache_dir_removed_before_finalize(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    guard = WorkspaceIntegrityGuard(repo)
    cache_file = repo / ".validator_cache" / "state.json"
    cache_file.parent.mkdir()
    cache_file.write_text("{}", encoding="utf-8")
    guard.record_write(path=".validator_cache/state.json", operation="create", actor="validator", intentionally_created=False, reason="validator cache")
    result = guard.check_and_cleanup()
    assert not cache_file.exists()
    assert any(path.startswith(".validator_cache") for path in result.removed_paths)


def test_model_created_source_preserved_when_intentional(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    guard = WorkspaceIntegrityGuard(repo)
    module = repo / "src" / "new_module.py"
    module.parent.mkdir()
    module.write_text("VALUE = 1\n", encoding="utf-8")
    guard.record_write(path="src/new_module.py", operation="create", actor="model patch", intentionally_created=True, reason="new module required")
    result = guard.check_and_cleanup()
    assert module.exists()
    assert result.finalize_success


def test_model_created_test_preserved_when_intentional(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    guard = WorkspaceIntegrityGuard(repo)
    test_file = repo / "tests" / "test_regression.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    guard.record_write(path="tests/test_regression.py", operation="create", actor="model patch", intentionally_created=True, reason="regression test requested")
    result = guard.check_and_cleanup()
    assert test_file.exists()
    assert result.finalize_success


def test_preexisting_untracked_user_file_preserved(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "notes.txt").write_text("keep", encoding="utf-8")
    guard = WorkspaceIntegrityGuard(repo)
    result = guard.check_and_cleanup()
    assert (repo / "notes.txt").exists()
    assert "notes.txt" not in result.removed_paths


def test_only_accidental_artifacts_do_not_finalize_success(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    guard = WorkspaceIntegrityGuard(repo)
    (repo / "artifact.log").write_text("noise", encoding="utf-8")
    guard.record_write(path="artifact.log", operation="create", actor="runner", intentionally_created=False, reason="command output")
    result = guard.check_and_cleanup()
    assert not result.finalize_success
    assert not result.final_changed_paths
