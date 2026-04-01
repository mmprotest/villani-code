from __future__ import annotations

from pathlib import Path

from villani_code.utils import is_path_within


def test_is_path_within_accepts_true_child_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    child = repo / "src" / "app.py"
    child.parent.mkdir(parents=True)
    child.write_text("x=1\n", encoding="utf-8")
    assert is_path_within(repo, child)


def test_is_path_within_rejects_prefix_attack_sibling(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sibling = tmp_path / "repo_evil" / "src" / "app.py"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("x=1\n", encoding="utf-8")
    assert not is_path_within(repo, sibling)


def test_is_path_within_handles_normalized_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "src" / "app.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("x=1\n", encoding="utf-8")
    candidate = repo / "src" / "." / ".." / "src" / "app.py"
    assert is_path_within(repo, candidate)
