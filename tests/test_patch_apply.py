from pathlib import Path

import pytest

from villani_code.patch_apply import PatchApplyError, apply_unified_diff, apply_unified_diff_with_diagnostics, parse_unified_diff


def test_apply_patch_simple_modify(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello\nworld\n", encoding="utf-8")
    diff = """--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 hello
-world
+villani
"""
    apply_unified_diff(tmp_path, diff)
    assert f.read_text(encoding="utf-8") == "hello\nvillani\n"


def test_apply_patch_crlf_file(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello\r\nworld\r\n", encoding="utf-8", newline="")
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n hello\n-world\n+there\n"
    apply_unified_diff(tmp_path, diff)
    assert f.read_bytes().decode("utf-8") == "hello\r\nthere\r\n"


def test_apply_patch_mismatch_fails(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello\nworld\n", encoding="utf-8")
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n hello\n-nope\n+there\n"
    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, diff)


def test_parse_unified_diff_accepts_git_headers_and_prose() -> None:
    diff = (
        "Here is your patch:\n"
        "diff --git a/a.txt b/a.txt\n"
        "index abc..def 100644\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1 +1 @@\n"
        "-hello\n"
        "+hi\n"
        "Thanks!\n"
    )
    patches = parse_unified_diff(diff)
    assert len(patches) == 1
    assert patches[0].new_path == "a.txt"


def test_parse_unified_diff_reports_malformed_hunk_details() -> None:
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ bad-hunk @@\n"
    with pytest.raises(PatchApplyError) as exc:
        parse_unified_diff(diff)
    assert "malformed hunk header" in str(exc.value)


def test_apply_patch_preserves_no_trailing_newline(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello\nworld", encoding="utf-8", newline="")
    diff = (
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " hello\n"
        "-world\n"
        "\\ No newline at end of file\n"
        "+there\n"
        "\\ No newline at end of file\n"
    )
    apply_unified_diff(tmp_path, diff)
    assert f.read_bytes().decode("utf-8") == "hello\nthere"


def test_apply_patch_fuzzy_whitespace_unique_candidate(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("alpha\nbeta  value\ngamma\n", encoding="utf-8")
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n alpha\n-beta value\n+beta changed\n gamma\n"
    touched, diagnostics = apply_unified_diff_with_diagnostics(tmp_path, diff)
    assert touched
    assert diagnostics.fallback_files == ["a.txt"]
    assert f.read_text(encoding="utf-8") == "alpha\nbeta changed\ngamma\n"


def test_apply_patch_fuzzy_ambiguous_candidate_fails(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("one\na  b\nx\na  b\ny\n", encoding="utf-8")
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -2,1 +2,1 @@\n-a b\n+z\n"
    with pytest.raises(PatchApplyError) as exc:
        apply_unified_diff(tmp_path, diff)
    assert "ambiguous" in str(exc.value)


def test_apply_patch_fuzzy_large_displacement_fails(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x\n" * 20 + "target  value\n", encoding="utf-8")
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-target value\n+done\n"
    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, diff)
