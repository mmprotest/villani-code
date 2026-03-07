from __future__ import annotations

import re
from pathlib import Path


IMPORT_UI_PATTERN = re.compile(r"^\s*(?:from\s+ui\b|import\s+ui\b)", re.MULTILINE)


def test_no_top_level_ui_compatibility_package_remains() -> None:
    assert not Path("ui").exists()


def test_internal_code_has_no_top_level_ui_imports() -> None:
    roots = [Path("villani_code"), Path("tests")]
    for root in roots:
        for file in root.rglob("*.py"):
            body = file.read_text(encoding="utf-8", errors="replace")
            assert IMPORT_UI_PATTERN.search(body) is None, f"legacy ui import found in {file}"
