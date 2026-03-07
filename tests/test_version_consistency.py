from __future__ import annotations

import tomllib
from pathlib import Path

import villani_code


def test_pyproject_version_matches_runtime_version() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == villani_code.__version__
