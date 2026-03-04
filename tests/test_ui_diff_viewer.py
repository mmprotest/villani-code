from pathlib import Path

from ui.diff_viewer import DiffViewer


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
