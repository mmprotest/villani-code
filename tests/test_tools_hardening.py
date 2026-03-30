from pathlib import Path

import pytest

from villani_code.tools import GitSimpleInput, LsInput, _safe_path


def test_safe_path_accepts_in_repo_path(tmp_path: Path) -> None:
    nested = tmp_path / "dir" / "file.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("x", encoding="utf-8")
    assert _safe_path(tmp_path, "dir/file.txt") == nested.resolve()


def test_safe_path_rejects_sibling_prefix_escape(tmp_path: Path) -> None:
    sibling = tmp_path.parent / f"{tmp_path.name}-evil"
    sibling.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="Path escapes"):
        _safe_path(tmp_path, f"../{sibling.name}/loot.txt")


def test_safe_path_allows_absolute_inside_active_root(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    target = sandbox / "src" / "game.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('hi')\n", encoding="utf-8")
    assert _safe_path(sandbox, str(target)) == target.resolve()


def test_safe_path_rejects_absolute_outside_active_root(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Path escapes workspace"):
        _safe_path(sandbox, str(outside))


def test_model_defaults_do_not_share_list_state() -> None:
    ls_one = LsInput()
    ls_two = LsInput()
    ls_one.ignore.append("custom")
    assert "custom" not in ls_two.ignore

    git_one = GitSimpleInput()
    git_two = GitSimpleInput()
    git_one.args.append("status")
    assert git_two.args == []
