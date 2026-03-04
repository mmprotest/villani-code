from pathlib import Path

import pytest

from villani_code.patch_apply import PatchApplyError, apply_unified_diff


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
    assert "there" in f.read_text(encoding="utf-8")


def test_apply_patch_mismatch_fails(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello\nworld\n", encoding="utf-8")
    diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n hello\n-nope\n+there\n"
    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, diff)
