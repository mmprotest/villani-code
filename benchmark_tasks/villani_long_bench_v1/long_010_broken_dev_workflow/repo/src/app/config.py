from __future__ import annotations
from pathlib import Path

def default_config_path(project_root: Path) -> Path:
    return project_root / 'config' / 'dev.json'
