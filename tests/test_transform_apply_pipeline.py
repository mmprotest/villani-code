from pathlib import Path

from villani_code.runtime.transform_apply import apply_model_transform


def test_stage1_succeeds_from_full_corrected_file_contents(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")

    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output="x=2\n",
    )
    assert result.success is True
    assert result.apply_mode == "full_file"
    assert result.changed_line_count > 0


def test_stage1_succeeds_from_snippet_replacement(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    patch = "SNIPPET_REPLACE\nFILE: src/app.py\nOLD_SNIPPET:\nx=1\nNEW_SNIPPET:\nx=2\n"

    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output=patch,
    )
    assert result.success is True
    assert result.apply_mode == "snippet_replace"


def test_stage1_succeeds_from_unified_diff(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    patch = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x=1\n+x=2"

    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output=patch,
    )
    assert result.success is True
    assert result.apply_mode == "unified_diff"


def test_stage1_noop_is_cleanly_identified(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")

    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output="x=1\n",
    )
    assert result.success is True
    assert result.diff_text == ""
    assert result.changed_line_count == 0


def test_snippet_old_text_must_match_exactly(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    patch = "SNIPPET_REPLACE\nFILE: src/app.py\nOLD_SNIPPET:\nx=3\nNEW_SNIPPET:\nx=2\n"

    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output=patch,
    )
    assert result.success is False
    assert result.parse_failure_reason == "snippet_old_block_not_found"
