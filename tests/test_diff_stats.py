from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from villani_code.benchmark import diff_stats


def test_count_utf8_lines_handles_permission_denied(monkeypatch, tmp_path: Path) -> None:
    blocked = tmp_path / "blocked.txt"
    blocked.write_text("secret\n", encoding="utf-8")

    original_read_text = Path.read_text

    def patched_read_text(path_obj: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path_obj == blocked:
            raise PermissionError("denied")
        return original_read_text(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", patched_read_text)

    assert diff_stats._count_utf8_lines(blocked) is None


def test_line_stats_skips_directory_unreadable_and_binary_paths(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "note.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\x80\x81\x82")
    blocked = tmp_path / "blocked.txt"
    blocked.write_text("hidden\n", encoding="utf-8")

    monkeypatch.setattr(
        diff_stats,
        "_run",
        lambda _repo, _args: "app\nbin.dat\nnote.txt\nblocked.txt\nmissing.txt\n",
    )
    monkeypatch.setattr(diff_stats.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=""))

    original_read_text = Path.read_text

    def patched_read_text(path_obj: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path_obj == blocked:
            raise PermissionError("denied")
        return original_read_text(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", patched_read_text)

    added, deleted = diff_stats.line_stats(tmp_path)

    assert (added, deleted) == (2, 0)
