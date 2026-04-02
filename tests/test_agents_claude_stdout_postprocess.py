from __future__ import annotations

import json
from pathlib import Path

from villani_code.benchmark.agents.cli_postprocess import apply_stdout_diff_if_needed, extract_unified_diff_from_stdout


def test_extract_diff_from_json_field() -> None:
    payload = {"result": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"}
    result = extract_unified_diff_from_stdout(json.dumps(payload))
    assert result.diff_text is not None
    assert result.diagnostics["json_detected"] is True


def test_extract_diff_from_raw_text() -> None:
    stdout = "note\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"
    result = extract_unified_diff_from_stdout(stdout)
    assert result.diff_text is not None


def test_empty_json_result_yields_no_diff() -> None:
    payload = {"result": ""}
    result = extract_unified_diff_from_stdout(json.dumps(payload))
    assert result.diff_text is None


def test_apply_stdout_diff_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("old\n", encoding="utf-8")
    stdout = json.dumps({"result": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n"})
    touched, diagnostics = apply_stdout_diff_if_needed(repo, stdout)
    assert diagnostics["applied"] is True
    assert any(path.endswith("a.txt") for path in touched)
    assert (repo / "a.txt").read_text(encoding="utf-8") == "new\n"
