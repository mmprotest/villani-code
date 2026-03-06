from pathlib import Path

from villani_code.tui.components.diff_viewer import DiffViewer


def test_diff_parse_and_fold() -> None:
    viewer = DiffViewer(Path("."))
    fixture = Path("tests/fixtures/sample.diff").read_text(encoding="utf-8")
    files = viewer.parse(fixture)
    assert files
    hunk = files[0].hunks[0]
    folded = viewer.fold_hunk(hunk, context_lines=2)
    assert folded.folded is True
    rendered = viewer.render_plain(files)
    assert "[green]" in rendered or "[red]" in rendered


def test_parse_unified_diff_without_diff_git_header() -> None:
    viewer = DiffViewer(Path("."))
    text = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"
    files = viewer.parse(text)
    assert files and files[0].path == "a.txt"
