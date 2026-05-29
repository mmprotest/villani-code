"""Frozen Villani runtime entrypoint for PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None and not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from villani_code.cli import app


if __name__ == "__main__":
    app()
