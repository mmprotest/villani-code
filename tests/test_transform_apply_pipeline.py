import json
from pathlib import Path

from villani_code.runtime.transform_apply import apply_model_transform


def test_stage1_succeeds_from_full_file_proposal(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")

    proposal = json.dumps(
        {
            "mode": "full_file",
            "file_path": "src/app.py",
            "new_content": "x=2\n",
            "old_snippet": None,
            "new_snippet": None,
            "rationale": "fix",
        }
    )
    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output=proposal,
    )
    assert result.success is True
    assert result.apply_mode == "full_file"
    assert result.changed_line_count > 0


def test_stage1_succeeds_from_snippet_replacement_proposal(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    proposal = json.dumps(
        {
            "mode": "snippet_replace",
            "file_path": "src/app.py",
            "new_content": None,
            "old_snippet": "x=1\n",
            "new_snippet": "x=2\n",
            "rationale": "fix",
        }
    )

    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output=proposal,
    )
    assert result.success is True
    assert result.apply_mode == "snippet_replace"


def test_stage1_noop_is_cleanly_identified(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    proposal = json.dumps(
        {
            "mode": "full_file",
            "file_path": "src/app.py",
            "new_content": "x=1\n",
            "old_snippet": None,
            "new_snippet": None,
            "rationale": None,
        }
    )

    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output=proposal,
    )
    assert result.success is True
    assert result.diff_text == ""
    assert result.changed_line_count == 0


def test_snippet_old_text_must_match_exactly(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "src" / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    proposal = json.dumps(
        {
            "mode": "snippet_replace",
            "file_path": "src/app.py",
            "new_content": None,
            "old_snippet": "x=3\n",
            "new_snippet": "x=2\n",
            "rationale": "fix",
        }
    )

    result = apply_model_transform(
        workspace=tmp_path,
        target_file="src/app.py",
        current_content="x=1\n",
        model_output=proposal,
    )
    assert result.success is False
    assert result.parse_failure_reason == "proposal_old_snippet_not_found"
