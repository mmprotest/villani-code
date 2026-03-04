from pathlib import Path

from villani_code.tools import ReadInput, _run_read


def test_read_respects_offset_and_limit(tmp_path: Path):
    path = tmp_path / "sample.txt"
    path.write_text("a\nb\nc\nd\n", encoding="utf-8")
    out = _run_read(ReadInput(file_path="sample.txt", offset_lines=1, limit_lines=2, max_bytes=1000), tmp_path)
    assert out.splitlines() == ["2: b", "3: c"]


def test_read_truncates_and_adds_hint(tmp_path: Path):
    path = tmp_path / "big.txt"
    path.write_text("\n".join(["line" + str(i) for i in range(100)]), encoding="utf-8")
    out = _run_read(ReadInput(file_path="big.txt", max_bytes=60), tmp_path)
    assert "...[truncated]" in out
    assert "offset_lines" in out
